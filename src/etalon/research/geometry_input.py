"""Declarative geometry semantics inside the existing scientific-input envelope.

This stdlib-only validator does not infer chemical state, choose flexible bonds,
prepare a receptor, or run an optimizer. Domain consumers must also verify graph
cuts, chemical typing and measured hard invariants against the actual molecule.
"""
from __future__ import annotations
import json
import math

SCHEMA = 'etalon-molecular-geometry/1'


def geometry_evidence_ids(bundle):
    """Return only ancestors of the execution geometry binding, not all records.

    Call the existing scientific-input compiler first to enforce role/file checks.
    A subconstraint cannot cite an unrelated evaluation/context record merely
    because that ID exists somewhere in the envelope.
    """
    records={r['id']:r for layer in ('sources','observations','interpretations') for r in bundle[layer]}
    bindings=[r for r in bundle['bindings'] if r['target']=='geometry' and r['use']=='constraint']
    if len(bindings)!=1:raise ValueError('one execution geometry binding required')
    found=set();pending=list(bindings[0]['basis_ids'])
    while pending:
        rid=pending.pop()
        if rid in found:continue
        if rid not in records or records[rid]['role']!='conditioning':raise ValueError('geometry lineage must be conditioning evidence')
        found.add(rid);pending.extend(records[rid].get('basis_ids',[]))
    return found


def _finite(x):
    if type(x) not in (float, int) or not math.isfinite(x): raise ValueError('finite number required')
    return float(x)


def _indices(value, count, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value): raise ValueError('explicit atom indices required')
    if len(set(value)) != len(value) or any(type(i) is not int or not 0 <= i < count for i in value):
        raise ValueError('duplicate or out-of-range atom index')
    return value


def _xyz(value):
    if not isinstance(value, list) or len(value) != 3: raise ValueError('explicit xyz required')
    for v in value: _finite(v)


def validate_geometry(value, *, available_evidence_ids=None):
    """Fail closed on ambiguous units, unsupported hard semantics and provenance."""
    expected = {'schema','representation','atom_count','atom_order_digest','coordinate_frame','chemical_state',
                'hard','dofs','soft_positions','soft_distances','directions','direction_exclusions','scope'}
    if set(value) != expected or value['schema'] != SCHEMA: raise ValueError('unsupported geometry fields/schema')
    if value['representation'] != 'bounded_bond_rotations': raise ValueError('unsupported coordinate representation')
    n = value['atom_count']
    if type(n) is not int or n < 1: raise ValueError('invalid atom count')
    if len(value['atom_order_digest']) != 64: raise ValueError('atom-order identity required')
    frame = value['coordinate_frame']
    if not frame.get('sha256') or not frame.get('bytes'): raise ValueError('coordinate-frame identity required')
    if not value['scope'] or not value['chemical_state'].get('assumptions'): raise ValueError('chemical-state scope required')
    hard = value['hard']
    if set(hard) != {'fixed_indices','reference','preserve','position_tolerance_A','bond_tolerance_A','angle_tolerance_deg'}:
        raise ValueError('unsupported hard constraint')
    fixed = _indices(hard['fixed_indices'],n,nonempty=True)
    if hard['reference'] != 'per_execution_input' or hard['preserve'] != 'bond_lengths_angles_and_rings':
        raise ValueError('hard reference and invariants must be explicit')
    for key in ('position_tolerance_A','bond_tolerance_A','angle_tolerance_deg'):
        if not 0 < _finite(hard[key]) <= 1e-4: raise ValueError('hard invariant tolerance too loose')
    ids = set(); movable = set()
    for dof in value['dofs']:
        if set(dof) != {'id','axis','moving_indices','bounds_degrees','zero','evidence_ids','rationale'}:
            raise ValueError('unsupported degree-of-freedom fields')
        if not dof['id'] or dof['id'] in ids: raise ValueError('duplicate degree of freedom')
        ids.add(dof['id']); axis=_indices(dof['axis'],n,nonempty=True)
        if len(axis)!=2: raise ValueError('bond axis needs two atoms')
        moving=_indices(dof['moving_indices'],n,nonempty=True)
        if set(moving)&(set(axis)|set(fixed)): raise ValueError('moving group conflicts with hard-fixed atoms or axis')
        bounds=dof['bounds_degrees']
        if len(bounds)!=2 or not -180 <= _finite(bounds[0]) < 0 < _finite(bounds[1]) <= 180:
            raise ValueError('explicit bounded rotation around the input is required')
        if dof['zero']!='input_pose' or not dof['rationale']: raise ValueError('rotation origin/rationale required')
        movable.update(moving)
    if not value['dofs'] or set(fixed)|movable != set(range(n)):
        raise ValueError('every atom must be fixed or assigned to an explicit moving component')
    for row in value['soft_positions']:
        if set(row)!={'index','xyz','flat_bottom_A','k_kcal_mol_A2','evidence_ids'}: raise ValueError('unsupported soft position fields')
        _indices([row['index']],n);_xyz(row['xyz'])
        if _finite(row['flat_bottom_A'])<0 or _finite(row['k_kcal_mol_A2'])<=0: raise ValueError('invalid soft position')
    for row in value['soft_distances']:
        if set(row)!={'id','ligand_index','protein_key','protein_xyz','lower_A','upper_A','k_kcal_mol_A2','interpretation','evidence_ids'}:
            raise ValueError('unsupported soft distance fields')
        _indices([row['ligand_index']],n);_xyz(row['protein_xyz'])
        if not 0<_finite(row['lower_A'])<_finite(row['upper_A']) or _finite(row['k_kcal_mol_A2'])<=0: raise ValueError('invalid distance interval')
        if row['interpretation']!='reported_contact_distance': raise ValueError('distance alone cannot certify a hydrogen bond')
    for row in value['directions']:
        if set(row)!={'id','kind','acceptor_index','acceptor_type','donor_key','donor_xyz','hydrogen_xyz','hydrogen_origin',
            'donor_state_assumption','target_DHA_degrees','k_DHA_kcal_mol_rad2','acceptor_neighbors','k_axis_kcal_mol_rad2','evidence_ids'}:
            raise ValueError('unsupported directional fields')
        _indices([row['acceptor_index']],n);_xyz(row['donor_xyz']);_xyz(row['hydrogen_xyz'])
        if row['kind']!='explicit_DHA_proxy' or row['acceptor_type']!='graph_typed_acceptor': raise ValueError('direction requires a typed acceptor')
        if row['hydrogen_origin'] not in {'observed','constructed_planar'} or not row['donor_state_assumption']:
            raise ValueError('explicit hydrogen provenance and donor state required')
        if _finite(row['target_DHA_degrees'])!=180: raise ValueError('only the declared linear DHA proxy is supported')
        if _finite(row['k_DHA_kcal_mol_rad2'])<=0 or _finite(row['k_axis_kcal_mol_rad2'])<0: raise ValueError('invalid directional coefficients')
        _indices(row['acceptor_neighbors'],n)
        if row['k_axis_kcal_mol_rad2']>0 and len(row['acceptor_neighbors'])!=2:
            raise ValueError('aromatic acceptor axis needs two covalent neighbors')
    for row in value['direction_exclusions']:
        if not row.get('id') or not row.get('reason'): raise ValueError('excluded direction requires an explicit reason')
    for collection,key in [('soft_positions','index'),('soft_distances','id'),('directions','id')]:
        if len({row[key] for row in value[collection]})!=len(value[collection]):raise ValueError('duplicate geometry constraint')
    for collection in ('dofs','soft_positions','soft_distances','directions'):
        for row in value[collection]:
            if not row['evidence_ids'] or (available_evidence_ids is not None and not set(row['evidence_ids'])<=set(available_evidence_ids)):
                raise ValueError('missing geometry evidence lineage')
    return json.loads(json.dumps(value,allow_nan=False))
