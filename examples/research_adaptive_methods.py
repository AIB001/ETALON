"""Optional exact substitutes for finite molecular search and contact questions.

These functions expose all means and thresholds to the caller. They neither
choose a research goal nor authorize computation. Contact geometry is a saved
coordinate readout, not an MD, affinity, or physical-realizability surrogate.
"""
from __future__ import annotations

import numpy as np


def fingerprint_threshold(queries, candidates, threshold=0.5, *, method="bulk"):
    """Exact binary Tanimoto threshold hits; optional popcount upper bound.

    For bit counts a,b, intersection <= min(a,b), hence T <= min(a,b)/max(a,b).
    Equality is retained. Unsupported fingerprint types use RDKit's reference.
    Original candidate order is preserved, including duplicates and ties.
    """
    from rdkit import DataStructs
    queries, candidates = list(queries), list(candidates)
    if not np.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Threshold must be between zero and one")
    if method not in ("bulk", "popcount"):
        raise ValueError("Unknown fingerprint method")
    from rdkit.DataStructs.cDataStructs import ExplicitBitVect
    supported = all(isinstance(fp, ExplicitBitVect) for fp in queries+candidates)
    if supported and len({fp.GetNumBits() for fp in queries+candidates}) > 1:
        raise ValueError("Equal fingerprint lengths required")
    fallback = method == "popcount" and not supported
    use_bound = method == "popcount" and supported
    counts = np.array([fp.GetNumOnBits() for fp in candidates]) if use_bound else None
    results, evaluated = [], 0
    for query in queries:
        if use_bound:
            count = query.GetNumOnBits()
            maximum = np.maximum(count, counts)
            bound = np.divide(np.minimum(count, counts), maximum,
                              out=np.ones(len(counts), float), where=maximum != 0)
            indices = np.flatnonzero(bound >= threshold).tolist()
        else:
            indices = list(range(len(candidates)))
        scores = DataStructs.BulkTanimotoSimilarity(query, [candidates[i] for i in indices])
        results.append([(i, float(score)) for i, score in zip(indices, scores) if score >= threshold])
        evaluated += len(indices)
    return dict(hits=results, evaluated_pairs=evaluated,
                skipped_pairs=len(queries)*len(candidates)-evaluated,
                method="bulk" if fallback else method,
                fallback="unsupported_fingerprint_type" if fallback else None)


def contact_hits(points, *, distance_A=3.0, angle_deg=150.0, method="exact"):
    """D,H,A points with arbitrary leading dimensions; inclusive endpoint.

    The bound method skips direction for distant contacts and uses cosine
    monotonicity. Values near either floating-point boundary use the exact
    arccos predicate. All input degeneracies are checked, including far pairs.
    """
    points = np.asarray(points, float)
    if points.shape[-2:] != (3, 3) or not np.isfinite(points).all():
        raise ValueError("Finite (..., donor/hydrogen/acceptor, xyz) points required")
    if not np.isfinite([distance_A, angle_deg]).all() or distance_A < 0 or not 0 <= angle_deg <= 180:
        raise ValueError("Finite nonnegative distance and angle in 0..180 required")
    if method not in ("exact", "bounded"):
        raise ValueError("Unknown contact method")
    d, h, a = (points[..., i, :] for i in range(3))
    dh, ah, da = d-h, a-h, d-a
    norm = np.linalg.norm(dh, axis=-1)*np.linalg.norm(ah, axis=-1)
    if np.any(norm < 1e-10):
        raise ValueError("Degenerate donor/hydrogen/acceptor geometry")
    distance = np.linalg.norm(da, axis=-1)
    if method == "exact":
        cosine = np.clip(np.sum(dh*ah, axis=-1)/norm, -1., 1.)
        angles = np.degrees(np.arccos(cosine))
        return dict(hit=(distance <= distance_A) & (angles >= angle_deg),
                    direction_evaluations=int(norm.size), boundary_fallbacks=0)
    eligible = distance <= distance_A
    hit = np.zeros(norm.shape, bool)
    cosine = np.clip(np.sum(dh[eligible]*ah[eligible], axis=-1)/norm[eligible], -1., 1.)
    threshold = np.cos(np.radians(angle_deg))
    value = cosine <= threshold
    boundary = np.abs(cosine-threshold) <= 64*np.finfo(float).eps
    value[boundary] = np.degrees(np.arccos(cosine[boundary])) >= angle_deg
    hit[eligible] = value
    return dict(hit=hit, direction_evaluations=int(eligible.sum()),
                boundary_fallbacks=int(boundary.sum()))


def translation_contact_search(xyz, triples, labels, ligand_indices, translations,
                               *, distance_A=3., angle_deg=150., method="exact"):
    """Evaluate every supplied rigid ligand translation on saved coordinates.

    If D/H move together and A is on the other molecule, distance can decrease
    by at most max|translation|, for either donor orientation. If an entire
    required label is farther than cutoff+bound,
    every proposal has joint=False. This proves exclusion for *any* displacement
    within that bound, but passing the bound is never a feasibility certificate.
    Returned triple hits are omitted for excluded frames: only the joint endpoint
    is promised. Unsupported topology automatically uses the exact calculation.
    """
    xyz, triples = np.asarray(xyz, float), np.asarray(triples, int)
    labels, translations = np.asarray(labels), np.asarray(translations, float)
    if xyz.ndim != 3 or xyz.shape[-1] != 3 or not np.isfinite(xyz).all():
        raise ValueError("Finite frame by atom by xyz coordinates required")
    if triples.ndim != 2 or triples.shape[1] != 3 or len(triples) != len(labels) or not len(triples):
        raise ValueError("Nonempty mapped triples and matching labels required")
    if np.any(triples < 0) or np.any(triples >= xyz.shape[1]):
        raise ValueError("Contact atom index outside coordinates")
    if translations.ndim != 2 or translations.shape[1] != 3 or not len(translations) or not np.isfinite(translations).all():
        raise ValueError("Nonempty finite translation vectors required")
    if method not in ("exact", "bounded"):
        raise ValueError("Unknown search method")
    moving = np.isin(triples, np.asarray(ligand_indices, int))
    supported = bool(((moving[:, 0] == moving[:, 1]) & (moving[:, 0] != moving[:, 2])).all())
    fallback = "topology_outside_intermolecular_intact_donor" if method == "bounded" and not supported else None
    points = xyz[:, triples]
    # Validate the initial geometry even if the subsequent bound skips a frame.
    dh, ah = points[:, :, 0]-points[:, :, 1], points[:, :, 2]-points[:, :, 1]
    if np.any(np.linalg.norm(dh, axis=-1)*np.linalg.norm(ah, axis=-1) < 1e-10):
        raise ValueError("Degenerate initial donor/hydrogen/acceptor geometry")
    names = sorted(set(labels.tolist()))
    excluded = np.zeros(len(xyz), bool)
    radius = float(np.linalg.norm(translations, axis=1).max())
    lower_bound = np.zeros((len(xyz), len(names)))
    if method == "bounded" and supported:
        distance = np.linalg.norm(points[:, :, 0]-points[:, :, 2], axis=-1)
        lower_bound = np.stack([distance[:, labels == label].min(axis=-1)-radius for label in names], axis=-1)
        # Inflate the feasible set around numerical boundaries, never prune ties.
        excluded = (lower_bound > distance_A + 64*np.finfo(float).eps*(1+distance_A+radius)).any(axis=-1)
        # Preserve reference errors: skipped proposals must also be unable to
        # make any H-A pair degenerate, even for a different contact label.
        safe_norm = (np.linalg.norm(ah, axis=-1)-radius)*np.linalg.norm(dh, axis=-1)
        excluded &= (safe_norm > 1e-10).all(axis=-1)
    selected = np.flatnonzero(~excluded)
    proposed = points[selected, None] + translations[None, :, None, None, :]*moving[None, None, :, :, None]
    geometry = contact_hits(proposed, distance_A=distance_A, angle_deg=angle_deg,
                            method="bounded" if method == "bounded" and supported else "exact")
    per_label = np.stack([geometry["hit"][..., labels == label].any(axis=-1) for label in names], axis=-1)
    joint = np.zeros((len(xyz), len(translations)), bool)
    joint[selected] = per_label.all(axis=-1)
    return dict(joint=joint, excluded_frames=excluded,
                label_distance_lower_bounds_A=lower_bound, displacement_bound_A=radius,
                direction_evaluations=geometry["direction_evaluations"],
                boundary_fallbacks=geometry["boundary_fallbacks"],
                evaluated_frame_proposals=int(len(selected)*len(translations)),
                total_frame_proposals=int(len(xyz)*len(translations)),
                fallback=fallback, method="exact" if fallback else method,
                scope="Finite rigid ligand translations; fixed protein; either donor orientation; geometric contacts only")


def anchor_projection(acceptor_from_hydrogen, donor_from_hydrogen, *,
                      distance_A=3.0, angle_deg=150.0, tolerance=1e-8):
    """Numerical distance certificate to the closed single-anchor contact set.

    Extracted from this task's native-host successor ``anchor_followup.py``.
    Project A-H onto the convex angular cone intersected with the D-A ball.
    The apex relaxes the nondegenerate contact predicate, so its inclusion is
    safe for exclusions. A caller may exclude a displacement budget only when
    lower_bound_A > budget + numerical_margin_A. This is not interval proof.
    Unsupported parameters or failed numerical checks raise ValueError: use
    the exact reference or retain unknown, never interpret failure as exclusion.
    Joint feasibility, sterics, energy and dynamics are outside this function.
    """
    w, dh = np.asarray(acceptor_from_hydrogen, float), np.asarray(donor_from_hydrogen, float)
    if w.shape != (3,) or dh.shape != (3,) or not np.isfinite([*w, *dh, distance_A, angle_deg, tolerance]).all():
        raise ValueError("Finite 3D relative coordinates and parameters required")
    ell = float(np.linalg.norm(dh))
    if not 0 < ell < distance_A or not 90 < angle_deg < 180 or tolerance <= 0:
        raise ValueError("Supported regime: 0 < |D-H| < distance_A, 90 < angle_deg < 180, tolerance > 0")
    u = -dh/ell
    cosine, sine = np.cos(np.radians(180-angle_deg)), np.sin(np.radians(180-angle_deg))
    z = float(w @ u); radial = w-z*u; rho = float(np.linalg.norm(radial))
    if rho > 1e-14:
        e = radial/rho
    else:
        axis = np.eye(3)[int(np.argmin(np.abs(u)))]; e = axis-(axis @ u)*u; e /= np.linalg.norm(e)
    qc = w.copy() if z >= 0 and rho*cosine <= z*sine else max(z*cosine+rho*sine, 0.)*(cosine*u+sine*e)
    nc = cosine*e-sine*u
    candidates = []
    if np.linalg.norm(qc-dh) <= distance_A+1e-12:
        candidates.append((qc, "cone"))
    delta = w-dh; length = float(np.linalg.norm(delta))
    qb = dh+distance_A*delta/length if length > 1e-14 else dh.copy()
    zb = float(qb @ u)
    if zb >= 0 and np.linalg.norm(qb-zb*u)*cosine <= zb*sine+1e-12:
        candidates.append((qb, "ball"))
    extent = -ell*cosine+np.sqrt(distance_A**2-ell**2*sine**2)
    candidates.append((extent*(cosine*u+sine*e), "rim"))
    q, boundary = min(candidates, key=lambda item: np.linalg.norm(w-item[0]))
    upper = float(np.linalg.norm(w-q)); polar_residual = 0.
    if upper < 1e-12:
        normal, support, lower = np.zeros(3), 0., 0.
    elif boundary == "cone":
        normal = (w-q)/upper; nz = float(normal @ u)
        polar_residual = max(nz*cosine+np.linalg.norm(normal-nz*u)*sine, 0.)
        support, lower = 0., float(normal @ w)
    else:
        nb = (q-dh)/np.linalg.norm(q-dh)
        if boundary == "ball":
            lam, mu = max(float((w-q) @ nb), 0.), 0.
        else:
            normals = np.stack([nb, nc], axis=1)
            try:
                lam, mu = np.maximum(np.linalg.solve(normals.T @ normals, normals.T @ (w-q)), 0.)
            except np.linalg.LinAlgError as error:
                raise ValueError("Numerically ambiguous support; retain unknown") from error
        normal = lam*nb+mu*nc; length = float(np.linalg.norm(normal))
        if length <= 1e-14:
            raise ValueError("Numerically unresolved supporting normal")
        support = float(lam*(nb @ dh+distance_A)/length); normal /= length
        lower = float(normal @ w-support)
    zq = float(q @ u)
    residual = max(-zq, np.linalg.norm(q-zq*u)*cosine-zq*sine,
                   np.linalg.norm(q-dh)-distance_A, 0.)
    gap = upper-max(lower, 0.)
    if not np.isfinite([upper, lower, support, residual, gap, *normal]).all() or residual > tolerance or polar_residual > tolerance or abs(gap) > tolerance:
        raise ValueError("Primal/dual numerical checks failed; retain unknown")
    return dict(projection_A=q, lower_bound_A=max(lower, 0.), upper_bound_A=upper,
                dual_unit_normal=normal, dual_support_A=support,
                cone_only_lower_bound_A=float(np.linalg.norm(w-qc)),
                primal_residual_A=float(residual), primal_dual_gap_A=gap,
                numerical_margin_A=float(tolerance), active_boundary=boundary,
                scope="Closed single-anchor geometric relaxation; numerical certificate, not interval arithmetic")


def _anchor_support(normal, dh, distance_A, angle_deg):
    """Support of the closed cone/ball intersection, including its apex.

    In the plane of the normal and cone axis a linear maximum is at the
    apex, circular rim, or unconstrained ball support if that is in the cone.
    This independent calculation makes cached normals safe to reuse: cached
    lower bounds and offsets are never accepted as authority.
    """
    n = np.asarray(normal, float)
    ell = np.linalg.norm(dh); u = -dh/ell
    c, s = np.cos(np.radians(180-angle_deg)), np.sin(np.radians(180-angle_deg))
    norm = np.linalg.norm(n)
    if norm == 0:
        return 0.
    axial = float(n @ u); radial = float(np.linalg.norm(n-axial*u))
    extent = -ell*c+np.sqrt(distance_A**2-ell**2*s*s)
    support = max(0., float(extent*(axial*c+radial*s)))
    q = dh+distance_A*n/norm; z = float(q @ u)
    if z >= 0 and np.linalg.norm(q-z*u)*c <= z*s:
        support = max(support, float(n @ dh+distance_A*norm))
    return support


def joint_anchor_feasibility(points, labels, moving, *, displacement_bound_A=.5,
                             distance_A=3., angle_deg=150., previous=None,
                             tolerance=1e-8, max_iterations=160,
                             max_combinations=4096, enrich_normals=True):
    """Find a common rigid translation for unions of geometric contact sets.

    ``points`` is (contacts, D/H/A, xyz), ``moving`` is (contacts, D/H/A),
    and a hit for every distinct label is required. D/H must move together,
    opposite A. Multiple alternatives per label and either donor are supported.
    ``previous`` optionally supplies saved dual_unit_normal, lower_bound_A and
    minimizing_translations. Every reused normal receives a freshly calculated
    support bound; lower bounds are compared for diagnostics, not trusted.

    Each surviving combination is convex. A bounded SLSQP search proposes a
    translation and supporting planes, never an exclusion on its own. A
    nonnegative combination of valid planes a.t<=b certifies impossibility
    inside |t|<=B only when sum(b)+B*|sum(a)| is strictly negative by a numerical
    margin. Failed solves, unsupported geometry and limits retain unknown.
    Certificates use floating arithmetic with stated inflation, not intervals.
    Exact arccos endpoint and strict displacement checks certify each witness.
    If projection supports alone are inconclusive, optional enrichment retains
    separate ball and cone-polar normals at the minimax candidate. Their
    nonnegative combinations can expose joint conflict hidden by a single
    projection normal. All primal candidates and dual attempts are saved.
    No sterics, energy, dynamics, or physical repair are implied.
    """
    from itertools import product
    from scipy.optimize import minimize
    p = np.asarray(points, float); labels = np.asarray(labels)
    raw_moving = np.asarray(moving)
    parameters = [displacement_bound_A, distance_A, angle_deg, tolerance]
    if (p.ndim != 3 or p.shape[1:] != (3, 3) or not len(p)
            or labels.shape != (len(p),) or raw_moving.shape != (len(p), 3)
            or not np.isfinite(p).all() or not np.isfinite(parameters).all()
            or not np.isin(raw_moving, [0, 1]).all()
            or displacement_bound_A < 0 or distance_A <= 0 or tolerance <= 0
            or not isinstance(max_iterations, int) or max_iterations < 1
            or not isinstance(max_combinations, int) or max_combinations < 1
            or not isinstance(enrich_normals, bool)):
        raise ValueError("Finite mapped contact coordinates and positive solver limits required")
    moving = raw_moving.astype(bool)
    d, h, a = (p[:, i] for i in range(3)); w, dh = a-h, d-h
    lengths = np.linalg.norm(dh, axis=1)
    names = sorted(set(labels.tolist()))
    stats = dict(saved_normals_reused=0, analytic_projections=0, support_evaluations=0,
                 exact_witness_checks=0, primal_solver_calls=0,
                 primal_function_evaluations=0, dual_solver_calls=0,
                 dual_function_evaluations=0, combinations_checked=0,
                 enriched_combinations=0, constraint_normals_enriched=0)
    out = dict(status="unknown", reason=None, witness=None, certificates=[],
               label_names=names, single_contact_exclusions=[], surviving_contacts={},
               primal_diagnostics=[], dual_diagnostics=[],
               cached_lower_max_difference_A=None, statistics=stats,
               settings=dict(displacement_bound_A=displacement_bound_A,
                             distance_A=distance_A, angle_deg=angle_deg,
                             tolerance=tolerance, max_iterations=max_iterations,
                             max_combinations=max_combinations, enrich_normals=enrich_normals),
               scope="Fixed partner and intact rigid translation; geometric contacts; numerical certificates, not interval proof")
    if not ((moving[:, 0] == moving[:, 1]) & (moving[:, 0] != moving[:, 2])).all():
        out['reason'] = 'unsupported_topology'; return out
    if not 90 < angle_deg < 180 or not ((lengths > 0) & (lengths < distance_A)).all():
        out['reason'] = 'unsupported_cone_ball_regime'; return out
    if np.any(lengths*np.linalg.norm(w, axis=1) < 1e-10):
        out['reason'] = 'degenerate_initial_geometry'; return out
    signs = moving[:, 2].astype(int)-moving[:, 1].astype(int)
    u = -dh/lengths[:, None]
    c, s = np.cos(np.radians(180-angle_deg)), np.sin(np.radians(180-angle_deg))
    B = displacement_bound_A
    if previous is not None:
        previous = {k: np.asarray(v, float) for k, v in previous.items()}
        for key, shape in [('dual_unit_normal', (len(p), 3)),
                           ('lower_bound_A', (len(p),)),
                           ('minimizing_translations', (len(p), 3))]:
            if key not in previous or previous[key].shape != shape or not np.isfinite(previous[key]).all():
                raise ValueError("Invalid saved single-anchor arrays")

    def plane(j, n):
        n = np.asarray(n, float)
        norm = np.linalg.norm(n)
        if norm == 0:
            return None
        n = n/norm
        support = _anchor_support(n, dh[j], distance_A, angle_deg)
        stats['support_evaluations'] += 1
        inflation = tolerance*(1+distance_A+float(np.linalg.norm(w[j])))
        return dict(contact_index=int(j), normal_q=n.tolist(),
                    normal_translation=(signs[j]*n).tolist(),
                    raw_support_A=float(support), inflation_A=inflation,
                    offset_A=float(support-n @ w[j]+inflation))

    def project(j, t):
        stats['analytic_projections'] += 1
        r = anchor_projection(w[j]+signs[j]*t, dh[j], distance_A=distance_A,
                              angle_deg=angle_deg, tolerance=tolerance)
        return signs[j]*(r['projection_A']-w[j]), plane(j, r['dual_unit_normal'])

    def exact_witness(t):
        stats['exact_witness_checks'] += 1
        norm = float(np.linalg.norm(t))
        if not np.isfinite(t).all() or norm > B:
            return None
        translated = p+t*moving[..., None]
        try:
            hit = contact_hits(translated, distance_A=distance_A, angle_deg=angle_deg)['hit']
        except ValueError:
            return None
        if not all(hit[labels == name].any() for name in names):
            return None
        contacts = {}
        for name in names:
            j = int(np.flatnonzero(hit & (labels == name))[0])
            dd, hh, aa = translated[j]
            distance = float(np.linalg.norm(dd-aa))
            angle = float(np.degrees(np.arccos(np.clip((dd-hh) @ (aa-hh)/
                          (np.linalg.norm(dd-hh)*np.linalg.norm(aa-hh)), -1., 1.))))
            contacts[str(name)] = dict(contact_index=j, distance_A=distance,
                                      angle_deg=angle, distance_margin_A=distance_A-distance,
                                      angle_margin_deg=angle-angle_deg)
        return dict(translation_A=t.tolist(), norm_A=norm,
                    displacement_margin_A=B-norm, contacts=contacts)

    initial_planes, seeds, differences = {}, {}, []
    for j in range(len(p)):
        try:
            if previous is None:
                seed, bound = project(j, np.zeros(3))
            else:
                stats['saved_normals_reused'] += 1
                seed = previous['minimizing_translations'][j]
                bound = plane(j, previous['dual_unit_normal'][j])
                derived = max(0., -bound['offset_A']-bound['inflation_A']) if bound else 0.
                differences.append(abs(derived-float(previous['lower_bound_A'][j])))
            seeds[j] = seed; initial_planes[j] = bound
            if bound and bound['offset_A']+B < -tolerance:
                out['single_contact_exclusions'].append(dict(contact_index=j, plane=bound,
                    contradiction_margin_A=float(-bound['offset_A']-B)))
        except ValueError:
            # Projection failure can cost speed, never produce an exclusion.
            seeds[j] = np.zeros(3); initial_planes[j] = None
    out['cached_lower_max_difference_A'] = max(differences) if differences else None
    excluded = {r['contact_index'] for r in out['single_contact_exclusions']}
    groups = [[int(j) for j in np.flatnonzero(labels == name) if j not in excluded] for name in names]
    out['surviving_contacts'] = {str(name): group for name, group in zip(names, groups)}
    if any(not group for group in groups):
        out.update(status='certified_unreachable', reason='all_contacts_for_required_label_excluded')
        return out
    combination_count = 1
    for group in groups:
        combination_count *= len(group)
    out['total_surviving_combinations'] = combination_count

    def dual_certificate(planes):
        if not planes:
            return None
        A = np.array([v['normal_translation'] for v in planes])
        b = np.array([v['offset_A'] for v in planes])
        def objective(lam):
            return float(lam @ b+B*np.linalg.norm(lam @ A))
        def derivative(lam):
            total = lam @ A; norm = np.linalg.norm(total)
            return b+B*(A @ total)/norm if norm > 1e-14 else b
        stats['dual_solver_calls'] += 1
        result = minimize(objective, np.full(len(b), 1./len(b)), jac=derivative,
                          bounds=[(0., 1.)]*len(b), method='SLSQP',
                          constraints=[dict(type='eq', fun=lambda x: x.sum()-1.,
                                            jac=lambda x: np.ones(len(x)))],
                          options=dict(ftol=1e-12, maxiter=max_iterations))
        stats['dual_function_evaluations'] += int(result.nfev)
        lam = np.maximum(result.x, 0.)
        if not np.isfinite(lam).all() or lam.sum() <= 0:
            return None
        lam /= lam.sum()
        margin = -objective(lam)
        out['dual_diagnostics'].append(dict(planes=planes.copy(), nonnegative_weights=lam.tolist(),
            contradiction_margin_A=float(margin), success=bool(result.success), status=int(result.status),
            message=str(result.message), iterations=int(result.nit), function_evaluations=int(result.nfev)))
        if margin > tolerance:
            return dict(planes=planes, nonnegative_weights=lam.tolist(),
                        weighted_normal=(lam @ A).tolist(),
                        weighted_offset_A=float(lam @ b),
                        contradiction_margin_A=float(margin),
                        solver_success=bool(result.success))
        return None

    unknown = 0
    for combination in product(*groups):
        if stats['combinations_checked'] >= max_combinations:
            out['reason'] = 'combination_limit'; break
        stats['combinations_checked'] += 1
        js = np.array(combination)
        planes = [initial_planes[j] for j in combination if initial_planes[j] is not None]
        certificate = dual_certificate(planes)
        if certificate is None:
            # Maximize shared interior margin. The hypographs are convex; only
            # the returned coordinates/planes, never solver status, are evidence.
            start = np.mean([seeds[j] for j in combination], axis=0)
            start *= min(1., B/max(float(np.linalg.norm(start)), 1e-15))
            def constraints(x):
                t, slack = x[:3], x[3]
                q = w[js]+signs[js, None]*t
                z = np.sum(q*u[js], axis=1)
                radial = np.linalg.norm(q-z[:, None]*u[js], axis=1)
                return np.r_[B-np.linalg.norm(t)-slack,
                             distance_A-np.linalg.norm(q-dh[js], axis=1)-slack,
                             z*s-radial*c-slack]
            x0 = np.r_[start, 0.]
            x0[3] = min(0., float(constraints(x0).min()))
            stats['primal_solver_calls'] += 1
            result = minimize(lambda x: -x[3], x0, jac=lambda x: np.array([0.,0.,0.,-1.]),
                              method='SLSQP', constraints=[dict(type='ineq', fun=constraints)],
                              options=dict(ftol=1e-12, maxiter=max_iterations))
            stats['primal_function_evaluations'] += int(result.nfev)
            out['primal_diagnostics'].append(dict(contact_indices=list(combination),
                translation_A=result.x[:3].tolist(), optimum_shared_slack_A=float(result.x[3]),
                constraint_residuals_A=constraints(result.x).tolist(),
                success=bool(result.success), status=int(result.status), message=str(result.message),
                iterations=int(result.nit), function_evaluations=int(result.nfev),
                start_translation_A=start.tolist()))
            for t in (result.x[:3], start):
                witness = exact_witness(t)
                if witness is not None:
                    out.update(status='witness', reason='exact_joint_endpoint', witness=witness)
                    return out
            if np.isfinite(result.x[:3]).all():
                for j in combination:
                    try:
                        _, bound = project(j, result.x[:3])
                        if bound is not None:
                            planes.append(bound)
                    except ValueError:
                        pass
            certificate = dual_certificate(planes)
            if certificate is None and enrich_normals and np.isfinite(result.x[:3]).all():
                stats['enriched_combinations'] += 1
                for j in combination:
                    q = w[j]+signs[j]*result.x[:3]
                    radial = q-float(q @ u[j])*u[j]
                    normals = [q-dh[j]]
                    if np.linalg.norm(radial) > 1e-14:
                        normals.append(c*radial/np.linalg.norm(radial)-s*u[j])
                    for normal in normals:
                        bound = plane(j, normal)
                        if bound is not None:
                            planes.append(bound)
                            stats['constraint_normals_enriched'] += 1
                certificate = dual_certificate(planes)
        if certificate is None:
            unknown += 1
        else:
            certificate['contact_indices'] = list(combination)
            out['certificates'].append(certificate)
    if stats['combinations_checked'] == combination_count and unknown == 0:
        out.update(status='certified_unreachable', reason='all_joint_combinations_certified')
    elif out['reason'] is None:
        out['reason'] = 'numerically_unresolved_combinations'
    out['unresolved_checked_combinations'] = unknown
    return out


def _joint_cli():
    """Optional file interface; only caller-specified inputs and output directory."""
    import argparse
    import hashlib
    import json
    from pathlib import Path
    import sys
    import time
    import scipy
    from scipy.optimize import minimize  # Freeze the native optimizer import state before reuse identity.
    from etalon.runtime.reuse import request_identity, execute_or_reuse
    wall, cpu = time.perf_counter(), time.process_time()
    parser = argparse.ArgumentParser(description='Joint rigid-translation contact feasibility')
    parser.add_argument('--input', required=True, help='Frozen NPZ contact arrays')
    parser.add_argument('--output', required=True, help='Fresh output directory; completed identical calls are read-only')
    args = parser.parse_args()
    source, input_path, out = Path(__file__).absolute(), Path(args.input).absolute(), Path(args.output).absolute()
    def identity(path):
        data = path.read_bytes()
        return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    pins = dict(source=identity(source), input=identity(input_path))
    with np.load(input_path, allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    settings = json.loads(str(arrays['settings_json'].item()))
    settings.setdefault('enrich_normals', True)
    import os
    dependency_files = sorted({str(Path(module.__file__).absolute()) for name, module in list(sys.modules.items())
                               if name.split('.')[0] in ('numpy', 'scipy', 'etalon') and getattr(module, '__file__', None)})
    environment = dict(python=sys.executable, python_version=sys.version,
                       interpreter=identity(Path(sys.executable)), numpy_version=np.__version__,
                       scipy_version=scipy.__version__, loaded_dependency_files=[identity(Path(p)) for p in dependency_files],
                       threads={key:os.environ.get(key) for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS')})
    request = request_identity(operation='joint-anchor-feasibility-batch', implementation=pins['source'],
                               environment=environment, inputs=pins['input'], parameters=settings)
    result_path = out/'result.json'
    def execute():
        if result_path.exists() or (out/'frames.json').exists():
            raise ValueError('Existing scientific outputs preserved; use a new output directory')
        records = []
        for index, frame_id in enumerate(arrays['frame_ids']):
            previous = dict(dual_unit_normal=arrays['previous_dual_unit_normal'][index],
                            lower_bound_A=arrays['previous_lower_bound_A'][index],
                            minimizing_translations=arrays['previous_minimizing_translations'][index])
            answer = joint_anchor_feasibility(arrays['points'][index], arrays['labels'], arrays['moving'],
                                             previous=previous, **settings)
            answer['frame_id'] = str(frame_id)
            answer['time_ps'] = float(arrays['time_ps'][index])
            records.append(answer)
        counts = {status: sum(r['status'] == status for r in records)
                  for status in ('witness', 'certified_unreachable', 'unknown')}
        (out/'frames.json').write_text(json.dumps(records, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        stats = {key: sum(r['statistics'][key] for r in records) for key in records[0]['statistics']}
        result = dict(schema='joint-anchor-analysis/1', status='complete', counts=counts,
                      frames=identity(out/'frames.json'), pins=pins, settings=settings,
                      statistics=stats, input_provenance=json.loads(str(arrays['provenance_json'].item())),
                      runtime=dict(python=sys.executable, numpy_version=np.__version__,
                                   numpy_path=np.__file__, scipy_version=scipy.__version__, scipy_path=scipy.__file__,
                                   method_path=str(source)), request_digest=request['request_digest'],
                      stage_cost=dict(wall_seconds=time.perf_counter()-wall, cpu_seconds=time.process_time()-cpu,
                                      scope='CLI body after imports through result assembly; process startup and final serialization excluded'),
                      limitations=['Historical correlated saved-coordinate reanalysis, no new independent data',
                                   'No new MD, docking, energy, sterics, affinity or physical-repair validation',
                                   'Floating-point support certificates with explicit inflation, not interval arithmetic',
                                   'Historical failures, threshold-adjacent witnesses and acquisition costs remain in prior artifacts'])
        result_path.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        return dict(result=result, artifacts=[str(result_path), str(out/'frames.json')])
    reuse = execute_or_reuse(out/'reuse', request, execute)
    receipt = reuse['receipt']; result = receipt['result']
    compact = dict(status=receipt['status'], reused=reuse['reused'], receipt_path=reuse['receipt_path'],
                   request_digest=request['request_digest'], call_cost=reuse['call_cost'],
                   original_execution_cost=receipt['execution_cost'], error=receipt['error'])
    if result is not None:
        compact.update(counts=result['counts'], statistics=result['statistics'], result=str(result_path),
                       frames=result['frames'], additional_solver_calls=0 if reuse['reused'] else
                       result['statistics']['primal_solver_calls']+result['statistics']['dual_solver_calls'])
    print(json.dumps(compact))
    if receipt['status'] != 'complete':
        raise SystemExit(1)


if __name__ == '__main__':
    _joint_cli()
