"""Reference-free CPU-pool diagnostics available before optional CNN scoring.

These are geometric descriptors, not a pose verifier or an affinity predictor.
The caller must separately prove when a decision was frozen. A retrospective
diagnostic never becomes a pre-execution choice merely by carrying this schema.
"""
from __future__ import annotations

import math
from pathlib import Path

from .evidence_refs import file_identity


def _receptor_atoms(path):
    coordinates, elements, residues = [], [], []
    model_count = 0
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith("MODEL "):
            model_count += 1
            if model_count > 1:
                raise ValueError("one receptor model required")
        if line[:6] not in {"ATOM  ", "HETATM"}:
            continue
        if line[16:17] not in {" ", "A"} or line[17:20].strip() in {"HOH", "WAT", "DOD"}:
            continue
        element = line[76:78].strip().upper()
        if not element:
            raise ValueError("explicit receptor element columns required")
        if element in {"H", "D"}:
            continue
        xyz = [float(line[a:b]) for a, b in ((30, 38), (38, 46), (46, 54))]
        if not all(math.isfinite(v) for v in xyz):
            raise ValueError("finite receptor coordinates required")
        coordinates.append(xyz)
        elements.append(element)
        residues.append((line[21:22], line[22:27], line[17:20]))
    if not coordinates:
        raise ValueError("no receptor heavy atoms")
    return coordinates, elements, residues


def diagnose_cpu_pool(pool_sdf, receptor_pdb, *, expected_pool, expected_receptor,
                      pocket_center, maximum_poses=100):
    """Read only pinned CPU poses and conditioning receptor; no gold/CNN inputs.

    ``minimizedAffinity`` is the sole permitted SDF property used numerically.
    Identity includes stereochemistry; diversity uses the existing fixed-frame,
    symmetry-aware RMSD helper against the best CPU pose, never a gold pose.
    Thresholds describe contacts, not scientific acceptance standards. Both files
    are rehashed after extraction. Missing/malformed inputs fail, never score zero.
    """
    import numpy as np
    from rdkit import Chem, rdBase
    from .pose_verifier import symmetry_rmsd

    if type(maximum_poses) is not int or not 1 <= maximum_poses <= 100:
        raise ValueError("maximum_poses must be 1..100")
    if (not isinstance(pocket_center, (list, tuple)) or len(pocket_center) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in pocket_center)):
        raise ValueError("finite three-dimensional pocket center required")
    pool, receptor = Path(pool_sdf), Path(receptor_pdb)
    before = {"pool": file_identity(pool), "receptor": file_identity(receptor)}
    if before != {"pool": expected_pool, "receptor": expected_receptor}:
        raise ValueError("conditioning identity mismatch")
    if before["pool"]["bytes"] > 16 * 1024 * 1024 or before["receptor"]["bytes"] > 32 * 1024 * 1024:
        raise ValueError("diagnostic input exceeds bounded file size")
    rec_xyz, rec_elements, residues = _receptor_atoms(receptor)
    if len(rec_xyz) > 200000:
        raise ValueError("receptor atom bound exceeded")
    rec_xyz = np.asarray(rec_xyz)
    rec_polar = np.asarray([e in {"N", "O", "S"} for e in rec_elements])
    molecules, rows = [], []
    chemical_identity = None
    for slot, original in enumerate(Chem.SDMolSupplier(str(pool), removeHs=False)):
        if slot >= maximum_poses:
            raise ValueError("pose bound exceeded; no silent truncation")
        if original is None or original.GetNumConformers() != 1:
            raise ValueError("one readable conformer required per pose")
        mol = Chem.RemoveHs(original)
        current = Chem.MolToSmiles(mol, isomericSmiles=True)
        if chemical_identity is not None and current != chemical_identity:
            raise ValueError("pool chemical identity/stereochemistry differs")
        chemical_identity = current
        if not 1 <= mol.GetNumHeavyAtoms() <= 256:
            raise ValueError("pose heavy atom bound exceeded")
        xyz = mol.GetConformer().GetPositions()
        if not np.isfinite(xyz).all():
            raise ValueError("finite pose coordinates required")
        if not original.HasProp("minimizedAffinity"):
            raise ValueError("explicit empirical minimizedAffinity required")
        score = float(original.GetProp("minimizedAffinity"))
        if not math.isfinite(score):
            raise ValueError("finite empirical score required")
        # Chunking bounds the temporary pairwise array for large receptors.
        nearest, clashes, polar_pairs, contacted = math.inf, 0, 0, set()
        ligand_polar = np.asarray([a.GetSymbol() in {"N", "O", "S"} for a in mol.GetAtoms()])
        for start in range(0, len(rec_xyz), 2048):
            delta = xyz[:, None, :] - rec_xyz[None, start:start + 2048, :]
            squared = np.einsum("ijk,ijk->ij", delta, delta)
            nearest = min(nearest, float(np.min(squared)))
            clashes += int(np.sum(squared < 1.6 ** 2))
            contact_indices = np.nonzero(np.any(squared <= 4.0 ** 2, axis=0))[0]
            contacted.update(residues[start + int(i)] for i in contact_indices)
            polar_pairs += int(np.sum((squared <= 3.5 ** 2) & ligand_polar[:, None]
                                      & rec_polar[None, start:start + 2048]))
        rows.append({"slot": slot, "empirical_score": score,
                     "nearest_receptor_heavy_A": math.sqrt(nearest),
                     "close_pairs_lt_1_6_A": clashes,
                     "contact_residues_le_4_A": len(contacted),
                     "polar_pairs_le_3_5_A": polar_pairs,
                     "pocket_center_distance_A": float(np.linalg.norm(xyz.mean(axis=0) - pocket_center))})
        molecules.append(mol)
    if not rows:
        raise ValueError("empty CPU pose pool")
    ordered = sorted(rows, key=lambda r: (r["empirical_score"], r["slot"]))
    top = ordered[0]
    diversity = [symmetry_rmsd(m, molecules[top["slot"]]) for m in molecules]
    values = {"pose_count": len(rows),
              "empirical_top_gap": ordered[1]["empirical_score"] - top["empirical_score"] if len(rows) > 1 else None,
              "empirical_span": ordered[-1]["empirical_score"] - top["empirical_score"],
              "top_nearest_receptor_heavy_A": top["nearest_receptor_heavy_A"],
              "top_close_pairs_lt_1_6_A": top["close_pairs_lt_1_6_A"],
              "top_contact_residues_le_4_A": top["contact_residues_le_4_A"],
              "top_polar_pairs_le_3_5_A": top["polar_pairs_le_3_5_A"],
              "top_pocket_center_distance_A": top["pocket_center_distance_A"],
              "pool_median_rmsd_from_cpu_top_A": float(np.median(diversity)),
              "pool_no_close_pairs_fraction": sum(r["close_pairs_lt_1_6_A"] == 0 for r in rows) / len(rows)}
    if before != {"pool": file_identity(pool), "receptor": file_identity(receptor)}:
        raise ValueError("conditioning file changed during diagnostic")
    return {"schema": "etalon-cpu-pose-pool-diagnostic/1",
            "availability_stage": "after_cpu_before_cnn", "decision_order_verified": False,
            "values": values, "poses": rows, "cpu_top_slot": top["slot"],
            "chemical_identity": chemical_identity, "inputs": before,
            "pocket_center": list(pocket_center), "rdkit_version": rdBase.rdkitVersion,
            "interpretation": {"polar_pairs": "distance-only N/O/S pairs, not directional hydrogen bonds",
                "close_pairs": "fixed geometric descriptor, not PoseBusters physical validity",
                "receptor": "nonwater heavy atoms; blank/A alternate locations; one model",
                "diversity": "fixed-frame symmetry RMSD relative to the CPU top pose, not gold"},
            "scope": "Conditioning descriptors only; no automatic selection, quality or affinity claim"}
