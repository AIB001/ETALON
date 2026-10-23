"""Independent, fixed-coordinate pose evaluation; never repairs a prediction."""
from __future__ import annotations

import math
from pathlib import Path

from .scientific_input import identity


def symmetry_rmsd(predicted, reference):
    """Symmetry-aware heavy atom RMSD without aligning the ligand to the answer."""
    from rdkit import Chem
    from rdkit.Chem import rdMolAlign
    pred, ref = Chem.RemoveHs(predicted), Chem.RemoveHs(reference)
    if Chem.MolToSmiles(pred, isomericSmiles=True) != Chem.MolToSmiles(ref, isomericSmiles=True):
        raise ValueError("molecular identity/stereochemistry differs from reference")
    before = pred.GetConformer().GetPositions().copy()
    value = rdMolAlign.CalcRMS(pred, ref, maxMatches=100000, symmetrizeConjugatedTerminalGroups=True)
    if not (before == pred.GetConformer().GetPositions()).all() or not math.isfinite(value):
        raise ValueError("verifier changed coordinates or returned non-finite RMSD")
    return float(value)


def verify_poses(predicted_sdf, reference_sdf, receptor_pdb, *, threshold=2.0):
    """Return all slots including failures; the target's reference is evaluator-only.

    Assumes receptor coordinates already match the experimental frame. This
    contract does not cover cross-docking or receptor alignment.
    """
    from rdkit import Chem
    from posebusters import PoseBusters
    import importlib.metadata
    files = {"predicted": str(predicted_sdf), "reference": str(reference_sdf), "receptor": str(receptor_pdb)}
    pins = {key: identity(path) for key, path in files.items()}
    ref = Chem.SDMolSupplier(str(reference_sdf), removeHs=False)[0]
    if ref is None:
        raise ValueError("unreadable evaluator reference")
    # Dock mode without mol_true runs validity checks independently of RMSD.
    bust = PoseBusters(config="dock")
    rows = []
    for index, mol in enumerate(Chem.SDMolSupplier(str(predicted_sdf), removeHs=False)):
        row = {"pose_index": index, "rmsd_A": None, "physical_valid": False,
               "success": False, "error": None, "checks": {}}
        try:
            if mol is None:
                raise ValueError("unreadable predicted molecule")
            row["rmsd_A"] = symmetry_rmsd(mol, ref)
            frame = bust.bust(mol_pred=mol, mol_cond=Path(receptor_pdb), full_report=False)
            checks = frame.iloc[0].to_dict()
            import numpy as np
            row["checks"] = {k: bool(v) for k, v in checks.items() if isinstance(v, (bool, np.bool_))}
            # Missing/nonboolean checks are unavailable, never a passing value.
            row["physical_valid"] = bool(checks) and len(row["checks"]) == len(checks) and all(row["checks"].values())
            row["success"] = row["physical_valid"] and row["rmsd_A"] <= threshold
        except Exception as exc:
            row["error"] = type(exc).__name__ + ": " + str(exc)
        rows.append(row)
    if pins != {key: identity(path) for key, path in files.items()}:
        raise ValueError("evaluator input changed")
    return {"schema": "etalon-pose-verification/1", "protocol": "fixed-receptor-frame-heavy-symmetry/1",
            "threshold_A": threshold, "posebusters_version": importlib.metadata.version("posebusters"),
            "inputs": pins, "rows": rows, "requested_slots": len(rows),
            "top1_success": bool(rows and rows[0]["success"]), "any_success": any(r["success"] for r in rows),
            "scope": "retrospective known-pocket redocking; no affinity or model-unseen claim"}
