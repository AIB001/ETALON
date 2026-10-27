"""Frozen, reference-free contact/relaxation ranking of an existing pose pool.

The score is an interpretable heuristic, not a force-field binding energy.
No gold pose, RMSD, checker result, or CNN output is accepted by this API.
"""
from __future__ import annotations

import math
from pathlib import Path

from etalon.runtime.files import file_identity as identity

METHOD = {
    "schema": "etalon-pose-ranking-method/1", "id": "contact-relaxation-v1",
    "empirical_window_kcal_mol": 1.0, "polar_distance_A": [2.5, 3.5],
    "contact_distance_A": 4.0, "severe_clash_distance_A": 1.6,
    "polar_elements": ["N", "O", "S"], "relaxation": "MMFF94s",
    "relaxation_max_iterations": 200, "relaxation_cap_kcal_mol": 50.0,
    "coefficients": {"empirical": 1.0, "relaxation": 0.10,
                     "polar_coverage": -0.25, "buried_unsatisfied_polar": 0.25,
                     "severe_clash_pairs": 5.0},
    "missing_policy": "explicit empirical fallback; never impute zero",
    "tie_break": "adjusted score, empirical score, original pose index",
}


def order_features(rows):
    """Select solely from complete numeric runtime features; retain all slots."""
    if not rows or any(type(r.get("pose_index")) is not int for r in rows):
        raise ValueError("nonempty, indexed pose features required")
    if {r["pose_index"] for r in rows} != set(range(len(rows))):
        raise ValueError("pose coverage must be exact")
    for row in rows:
        if type(row.get("empirical_score")) not in (int, float) or not math.isfinite(row["empirical_score"]):
            raise ValueError("finite empirical scores required for every slot")
    empirical = sorted(rows, key=lambda r: (r["empirical_score"], r["pose_index"]))
    eligible = [r for r in empirical if r["empirical_score"] <= empirical[0]["empirical_score"] + METHOD["empirical_window_kcal_mol"]]
    reasons = []
    for row in eligible:
        if row.get("error") or any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k]) or row[k] < 0
            for k in ("relaxation_energy_drop_kcal_mol", "polar_coverage", "buried_unsatisfied_polar", "severe_clash_pairs")):
            reasons.append({"pose_index": row["pose_index"], "error": row.get("error") or "missing/invalid feature"})
    scores = {}
    if reasons:
        ordered = empirical
    else:
        c = METHOD["coefficients"]
        for row in eligible:
            scores[row["pose_index"]] = (row["empirical_score"] + c["relaxation"] * min(row["relaxation_energy_drop_kcal_mol"], METHOD["relaxation_cap_kcal_mol"])
                + c["polar_coverage"] * row["polar_coverage"] + c["buried_unsatisfied_polar"] * row["buried_unsatisfied_polar"]
                + c["severe_clash_pairs"] * row["severe_clash_pairs"])
        ordered = sorted(eligible, key=lambda r: (scores[r["pose_index"]], r["empirical_score"], r["pose_index"]))
        ordered += [r for r in empirical if r not in eligible]
    return {"schema": "etalon-reference-free-pose-ranking/1", "method": METHOD,
            "ordered_pose_indices": [r["pose_index"] for r in ordered],
            "eligible_pose_indices": [r["pose_index"] for r in eligible],
            "adjusted_scores": {str(k): v for k, v in scores.items()},
            "fallback": bool(reasons), "fallback_reasons": reasons,
            "availability_stage": "after_cpu_pool_before_cnn_and_verifier"}


def rank_pool(pool_sdf, receptor_pdb, *, expected_pool, expected_receptor):
    """Extract features from the pinned native pool without modifying it.

    The receptor is conditioning input. Distance-only polar coverage does not
    establish a directional hydrogen bond. Relaxation is the nonnegative energy
    drop to a local vacuum minimum, not global conformational strain. Optimization
    touches a copy only; predicted coordinates and chemical state remain fixed.
    """
    import numpy as np
    from rdkit import Chem, rdBase
    from rdkit.Chem import AllChem

    paths = {"pool": Path(pool_sdf), "receptor": Path(receptor_pdb)}
    pins = {k: identity(v) for k, v in paths.items()}
    if pins != {"pool": expected_pool, "receptor": expected_receptor}:
        raise ValueError("conditioning identity mismatch")
    if pins["pool"]["bytes"] > 16 * 1024 * 1024 or pins["receptor"]["bytes"] > 32 * 1024 * 1024:
        raise ValueError("bounded input size exceeded")
    receptor, elements = [], []
    models = 0
    for line in paths["receptor"].read_text("ascii").splitlines():
        if line.startswith("MODEL "):
            models += 1
            if models > 1:
                raise ValueError("one receptor model required")
        if line[:6] not in {"ATOM  ", "HETATM"} or line[16:17] not in {" ", "A"} or line[17:20].strip() in {"HOH", "WAT", "DOD"}:
            continue
        element = line[76:78].strip().upper()
        if not element:
            raise ValueError("explicit receptor elements required")
        if element in {"H", "D"}:
            continue
        receptor.append([float(line[a:b]) for a, b in ((30, 38), (38, 46), (46, 54))])
        elements.append(element)
    xyzr = np.asarray(receptor)
    if not receptor or len(receptor) > 200000 or not np.isfinite(xyzr).all():
        raise ValueError("invalid or unbounded receptor")
    polar_r = np.asarray([e in METHOD["polar_elements"] for e in elements])
    rows, graph = [], None
    for index, original in enumerate(Chem.SDMolSupplier(str(paths["pool"]), removeHs=False)):
        if index >= 100 or original is None or original.GetNumConformers() != 1:
            raise ValueError("bounded readable single-conformer pool required")
        mol = Chem.RemoveHs(original)
        current = Chem.MolToSmiles(mol, isomericSmiles=True)
        if graph is not None and current != graph:
            raise ValueError("pool graph or stereochemistry differs")
        graph = current
        if not original.HasProp("minimizedAffinity"):
            raise ValueError("empirical score missing")
        row = {"pose_index": index, "empirical_score": float(original.GetProp("minimizedAffinity")), "error": None}
        try:
            xyz = mol.GetConformer().GetPositions()
            if not 1 <= mol.GetNumHeavyAtoms() <= 256 or not np.isfinite(xyz).all():
                raise ValueError("invalid ligand geometry")
            polar_l = np.asarray([a.GetSymbol() in METHOD["polar_elements"] for a in mol.GetAtoms()])
            covered = np.zeros(len(xyz), dtype=bool)
            buried = np.zeros(len(xyz), dtype=bool)
            clashes = 0
            for start in range(0, len(xyzr), 2048):
                delta = xyz[:, None, :] - xyzr[None, start:start + 2048, :]
                squared = np.einsum("ijk,ijk->ij", delta, delta)
                buried |= np.any(squared <= METHOD["contact_distance_A"] ** 2, axis=1)
                lo, hi = METHOD["polar_distance_A"]
                covered |= np.any((squared >= lo ** 2) & (squared <= hi ** 2) & polar_r[None, start:start + 2048], axis=1)
                clashes += int(np.sum(squared < METHOD["severe_clash_distance_A"] ** 2))
            row.update(polar_coverage=int(np.sum(polar_l & covered)),
                       buried_unsatisfied_polar=int(np.sum(polar_l & buried & ~covered)), severe_clash_pairs=clashes)
            relaxed = Chem.AddHs(Chem.Mol(mol), addCoords=True)
            properties = AllChem.MMFFGetMoleculeProperties(relaxed, mmffVariant=METHOD["relaxation"])
            if properties is None:
                raise ValueError("MMFF94s parameters unavailable")
            field = AllChem.MMFFGetMoleculeForceField(relaxed, properties)
            initial = float(field.CalcEnergy())
            status = int(field.Minimize(maxIts=METHOD["relaxation_max_iterations"]))
            final = float(field.CalcEnergy())
            if not all(math.isfinite(v) for v in (initial, final)) or status != 0:
                raise ValueError("MMFF94s local minimization not converged")
            row.update(relaxation_energy_drop_kcal_mol=max(0.0, initial - final), mmff_status=status,
                       mmff_initial_kcal_mol=initial, mmff_final_kcal_mol=final)
        except Exception as exc:
            row["error"] = type(exc).__name__ + ": " + str(exc)
        rows.append(row)
    result = order_features(rows)
    if pins != {k: identity(v) for k, v in paths.items()}:
        raise ValueError("conditioning input changed")
    return {**result, "inputs": pins, "features": rows, "chemical_identity": graph,
            "rdkit_version": rdBase.rdkitVersion, "reference_input": None,
            "interpretation": "heuristic same-pool ranking only; no affinity, hydrogen-bond or global strain claim"}
