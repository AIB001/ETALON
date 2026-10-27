"""Geometric motion decomposition of mapped, saved contact trajectories.

Factorial coordinate resets are descriptive counterfactuals. They need not be
sterically valid or dynamically realizable and do not identify causal forces.
No trajectory execution, chemical typing, or research-store policy lives here.
"""
from __future__ import annotations

import numpy as np

from .contacts import geometry


FACTORS = ("ligand_rigid", "ligand_internal", "protein_contact_atoms")


def rigid_fit(source, target):
    """Proper least-squares row-vector transform, rejecting ambiguous fits."""
    source, target = np.asarray(source, float), np.asarray(target, float)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("Matching N x 3 coordinates required")
    a, b = source.mean(0), target.mean(0)
    if min(np.linalg.matrix_rank(source-a), np.linalg.matrix_rank(target-b)) < 2:
        raise ValueError("At least three non-collinear fit atoms required")
    u, _, vt = np.linalg.svd((source-a).T @ (target-b))
    rotation = u @ np.diag([1., 1., np.linalg.det(u @ vt)]) @ vt
    return rotation, b-a @ rotation


def minimum_image(delta, box):
    fractional = np.asarray(delta) @ np.linalg.inv(box)
    return (fractional-np.rint(fractional)) @ box


def euclidean_geometry(xyz, triples):
    """D-A and D-H-A after the caller has established a common local image."""
    xyz, triples = np.asarray(xyz), np.asarray(triples, int)
    donor, hydrogen, acceptor = (xyz[triples[:, i]] for i in range(3))
    dh, ah = donor-hydrogen, acceptor-hydrogen
    norm = np.linalg.norm(dh, axis=1)*np.linalg.norm(ah, axis=1)
    if np.any(norm < 1e-10):
        raise ValueError("Degenerate donor/hydrogen/acceptor geometry")
    return np.linalg.norm(donor-acceptor, axis=1), np.degrees(np.arccos(
        np.clip(np.sum(dh*ah, axis=1)/norm, -1, 1)))


def reset_coordinates(aligned, reference, ligand_indices, core_indices, mask, *,
                      return_operation=False):
    """Reset chosen factors to reference; bits follow FACTORS.

    Internal geometry includes all departures from a core-defined rigid body;
    it is not assumed to be linker torsion. Full reset is the reference state.
    The historical third FACTORS label is retained for old readers; bit four
    actually resets every supplied non-ligand atom, not just contact atoms.
    Optional operation metadata is produced by the same assignments as the data.
    """
    if mask not in range(8):
        raise ValueError("Reset mask must be between zero and seven")
    ligand, core = np.asarray(ligand_indices, int), np.asarray(core_indices, int)
    rotation, shift = rigid_fit(reference[core], aligned[core])
    return _reset_with_fit(aligned, reference, ligand, mask, rotation, shift, return_operation)


def _reset_with_fit(aligned, reference, ligand, mask, rotation, shift, return_operation):
    result = np.array(aligned, copy=True)
    operation = dict(mask=int(mask), ligand_transform="unchanged",
                     ligand_target_indices=[], environment_reset_indices=[])
    if mask & 1 and mask & 2:
        result[ligand] = reference[ligand]
        operation["ligand_transform"] = "reference_coordinates"
    elif mask & 1:
        result[ligand] = (aligned[ligand]-shift) @ rotation.T
        operation["ligand_transform"] = "remove_core_rigid_transform"
    elif mask & 2:
        result[ligand] = reference[ligand] @ rotation+shift
        operation["ligand_transform"] = "reference_internal_at_current_core_transform"
    if mask & 3:
        operation["ligand_target_indices"] = ligand.tolist()
    if mask & 4:
        protein = np.ones(len(aligned), bool)
        protein[ligand] = False
        result[protein] = reference[protein]
        operation["environment_reset_indices"] = np.flatnonzero(protein).tolist()
    return (result, operation) if return_operation else result


def reset_family(aligned, reference, ligand_indices, core_indices, masks=range(8), *,
                 return_operations=False):
    """Evaluate a reset family with one shared core fit, retaining every state.

    This changes repeated numerical work, not which atoms or conditions are
    evaluated. Each returned array is independent and keeps the supplied order.
    """
    masks = tuple(masks)
    if any(mask not in range(8) for mask in masks):
        raise ValueError("Reset mask must be between zero and seven")
    ligand, core = np.asarray(ligand_indices, int), np.asarray(core_indices, int)
    rotation, shift = rigid_fit(reference[core], aligned[core])
    return [_reset_with_fit(aligned, reference, ligand, mask, rotation, shift, return_operations)
            for mask in masks]


def factorial_contacts(xyz_A, boxes_A, ligand_indices, core_indices,
                       alignment_indices, triples, labels, *, distance_A=3.,
                       angle_deg=150., tolerance=1e-7):
    """Align to frame zero, evaluate all eight resets, audit native geometry.

    Alignment atoms and contact triples must be chosen before evaluating the
    trajectory. Periodic images are anchored to frame zero. A local-image/native
    geometry mismatch raises instead of silently producing reset evidence.
    """
    xyz, boxes = np.asarray(xyz_A, float), np.asarray(boxes_A, float)
    ligand, core = np.asarray(ligand_indices, int), np.asarray(core_indices, int)
    align, triples = np.asarray(alignment_indices, int), np.asarray(triples, int)
    if not set(core).issubset(ligand) or set(align).intersection(ligand):
        raise ValueError("Core must belong to ligand and alignment to protein")
    if len(labels) != len(triples) or len(xyz) != len(boxes):
        raise ValueError("Inconsistent contact or frame mapping")
    origin = xyz[0, ligand[0]]
    reference = origin+minimum_image(xyz[0]-origin, boxes[0])
    names = sorted(set(labels))
    labels = np.asarray(labels)
    distance = np.empty((len(xyz), 8, len(triples)))
    angle = np.empty_like(distance)
    hit = np.empty((len(xyz), 8, len(names)), bool)
    aligned_series = np.empty_like(xyz)
    native_d, native_a = geometry(xyz, boxes, triples)
    metrics = {name: [] for name in ("core_translation_A", "core_rotation_deg",
               "internal_rmsd_A", "alignment_rmsd_A")}
    max_error = np.zeros(2)
    operations = []
    for frame, points in enumerate(xyz):
        whole = reference+minimum_image(points-reference, boxes[frame])
        rotation, shift = rigid_fit(whole[align], reference[align])
        aligned = whole @ rotation+shift
        aligned_series[frame] = aligned
        r, t = rigid_fit(reference[core], aligned[core])
        metrics["core_translation_A"].append(float(np.linalg.norm(
            aligned[core].mean(0)-reference[core].mean(0))))
        metrics["core_rotation_deg"].append(float(np.degrees(np.arccos(
            np.clip((np.trace(r)-1)/2, -1, 1)))))
        metrics["internal_rmsd_A"].append(float(np.sqrt(np.mean(np.sum(
            (aligned[ligand]-(reference[ligand] @ r+t))**2, axis=1)))))
        metrics["alignment_rmsd_A"].append(float(np.sqrt(np.mean(np.sum(
            (aligned[align]-reference[align])**2, axis=1)))))
        # The core transform was already needed for the motion metrics.
        for mask in range(8):
            reset, operation = _reset_with_fit(aligned, reference, ligand, mask, r, t, True)
            if frame == 0:
                operations.append(operation)
            d, a = euclidean_geometry(reset, triples)
            distance[frame, mask], angle[frame, mask] = d, a
            contacts = (d <= distance_A) & (a >= angle_deg)
            hit[frame, mask] = [contacts[labels == label].any() for label in names]
        error = [np.max(np.abs(distance[frame, 0]-native_d[frame])),
                 np.max(np.abs(angle[frame, 0]-native_a[frame]))]
        max_error = np.maximum(max_error, error)
        if max(error) > tolerance:
            raise ValueError("Local image changed native contact geometry")
    return dict(reference=reference, aligned=aligned_series, hit=hit,
                distance=distance, angle=angle, labels=names,
                metrics={k: np.asarray(v) for k, v in metrics.items()},
                max_native_error=max_error, operations=operations)


def minimal_reset_summary(hit, selected=None):
    """Count rescuable losses without imposing monotonicity or unique causes.

    A minimal successful mask has no successful proper subset. Multiple
    singleton or coupled possibilities remain explicit. Full-reset failure is
    reported as unresolved by reference, not attributed to an omitted force.
    """
    hit = np.asarray(hit, bool)
    if hit.ndim != 2 or hit.shape[1] != 8:
        raise ValueError("Expected frames by eight factorial conditions")
    selected = np.ones(len(hit), bool) if selected is None else np.asarray(selected, bool)
    observed = hit[selected]
    losses = observed[~observed[:, 0]]
    classes = dict(unique_single=0, multiple_single=0, coupled_only=0,
                   unresolved_by_reference=0)
    minimal_counts = {str(mask): 0 for mask in range(1, 8)}
    singleton_rescues = {name: 0 for name in FACTORS}
    full_reset_fails_but_partial_rescues = 0
    for row in losses:
        minimal = [mask for mask in range(1, 8) if row[mask] and not any(
            row[sub] for sub in range(1, mask) if sub & mask == sub)]
        for mask in minimal:
            minimal_counts[str(mask)] += 1
        singles = [i for i in range(3) if row[1 << i]]
        for i in singles:
            singleton_rescues[FACTORS[i]] += 1
        kind = ("unique_single" if len(singles) == 1 else "multiple_single" if singles
                else "coupled_only" if minimal else "unresolved_by_reference")
        classes[kind] += 1
        full_reset_fails_but_partial_rescues += int(bool(minimal) and not row[7])
    return dict(frames=int(len(observed)), observed_contact=int(observed[:, 0].sum()),
                lost_frames=int(len(losses)), classes=classes,
                singleton_rescues=singleton_rescues, minimal_mask_counts=minimal_counts,
                contact_by_reset={str(i): int(observed[:, i].sum()) for i in range(8)},
                full_reset_fails_but_partial_rescues=full_reset_fails_but_partial_rescues)
