"""Batched directional-contact readout; no execution or research-store dependency."""
from __future__ import annotations
import numpy as np


def geometry(xyz_A, boxes_A, triples):
    """Return donor/acceptor distance and D-H-A angle for each frame and triple.

    Uses the same fractional-coordinate minimum image as the native readout.
    Callers retain topology/chemical assignment and trajectory identity.
    """
    xyz, boxes, triples = np.asarray(xyz_A, dtype=float), np.asarray(boxes_A, dtype=float), np.asarray(triples, dtype=int)
    inverse = np.linalg.inv(boxes)
    lengths = np.diagonal(boxes, axis1=1, axis2=2)[:, None, :]
    orthogonal = np.count_nonzero(boxes * (1 - np.eye(3))) == 0
    def image(delta):
        if orthogonal:
            return delta - np.rint(delta / lengths) * lengths
        frac = delta @ inverse
        return (frac - np.rint(frac)) @ boxes
    donor, hydrogen, acceptor = (xyz[:, triples[:, i]] for i in range(3))
    dh, ah = image(donor-hydrogen), image(acceptor-hydrogen)
    norm = np.linalg.norm(dh, axis=-1)*np.linalg.norm(ah, axis=-1)
    if np.any(norm < 1e-10):
        raise ValueError('Degenerate donor/hydrogen/acceptor geometry')
    distance = np.linalg.norm(image(donor-acceptor), axis=-1)
    angle = np.degrees(np.arccos(np.clip(np.sum(dh*ah, axis=-1)/norm, -1, 1)))
    return distance, angle


def diagnose(distance, angle, residues, times_ps, *, distance_A=3., angle_deg=150., late_start_ps=50.):
    """Separate distance loss from angular loss over all assigned contact triples.

    Descriptive frames are correlated; these categories do not identify forces
    or estimate binding affinity. Thresholds are explicit, never optimized here.
    """
    distance, angle = np.asarray(distance), np.asarray(angle)
    names, times = np.asarray(residues), np.asarray(times_ps)
    near = distance <= distance_A
    contact = near & (angle >= angle_deg)
    rows = {}
    for residue in sorted(set(residues)):
        mask = names == residue
        close, held = near[:, mask].any(axis=1), contact[:, mask].any(axis=1)
        category = np.where(held, 0, np.where(close, 1, 2))
        def counts(selected):
            c = np.bincount(category[selected], minlength=3)
            return dict(contact=int(c[0]), angular_loss=int(c[1]), distance_loss=int(c[2]), frames=int(c.sum()))
        first = np.flatnonzero(~held)
        rows[residue] = dict(all=counts(np.ones(len(times), dtype=bool)),
            early=counts(times <= 5), late=counts(times >= late_start_ps),
            first_loss_ps=float(times[first[0]]) if len(first) else None,
            first_loss_kind=('angular_loss' if close[first[0]] else 'distance_loss') if len(first) else None)
    return rows
