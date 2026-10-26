"""Exact Cartesian pullback for the rooted, nested SND1 torsion map.

Only the kinematic Jacobian changes. The caller retains the old objective,
optimizer, bounds, chemistry, checks and Cartesian force-field gradients.
Directional proxy derivatives, if requested, retain their original finite
differences. Unsupported rotation order fails instead of silently extrapolating.
"""
from __future__ import annotations
import numpy as np


def validate_rooted_geometry(geometry):
    rows=geometry['dofs']
    for i,a in enumerate(rows):
        moving=set(a['moving_indices'])
        for b in rows[i+1:]:
            later=set(b['moving_indices'])
            if set(a['axis']) & later:
                raise ValueError('later rotation moves an earlier axis; reroot required')
            if moving & later and not later <= moving:
                raise ValueError('moving groups must be nested or disjoint')


def pullback(legacy,initial,geometry,q,cartesian_gradient,*,directional=False,epsilon=1e-6):
    validate_rooted_geometry(geometry)
    xyz=legacy.rotate(initial,geometry['dofs'],q);result=[]
    for k,row in enumerate(geometry['dofs']):
        a,b=row['axis'];axis=xyz[b]-xyz[a];axis=axis/np.linalg.norm(axis)
        indices=row['moving_indices']
        value=float(np.sum(cartesian_gradient[indices]*np.cross(axis,xyz[indices]-xyz[b])))
        if directional:
            lo,hi=np.deg2rad(row['bounds_degrees']);qa=np.array(q,copy=True);qb=qa.copy()
            qa[k]=min(hi,q[k]+epsilon);qb[k]=max(lo,q[k]-epsilon)
            value+=(sum(r['energy'] for r in legacy.direction_terms(legacy.rotate(initial,geometry['dofs'],qa),geometry))-
                    sum(r['energy'] for r in legacy.direction_terms(legacy.rotate(initial,geometry['dofs'],qb),geometry)))/(qa[k]-qb[k])
        result.append(value)
    return np.array(result)


def bind_to_isolated_module(legacy):
    """Use a private module instance, never mutate a shared loaded solver."""
    legacy.cartesian_chain_gradient=lambda initial,geometry,q,gradient,**kwargs:pullback(
        legacy,initial,geometry,q,gradient,**kwargs)
    return legacy
