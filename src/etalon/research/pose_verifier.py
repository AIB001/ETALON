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


def installed_pose_check_contract(configuration="dock"):
    """Freeze the actual installed checker, without observing any pose outcomes."""
    import importlib.metadata
    import posebusters
    from posebusters import PoseBusters
    from .result_integrity import freeze_check_contract
    package = Path(posebusters.__file__).parent
    implementation = {str(p): identity(p) for p in sorted(package.rglob("*"))
                      if p.is_file() and p.suffix in (".py", ".yml", ".yaml", ".json")}
    return freeze_check_contract(PoseBusters(config=configuration).config,
        version=importlib.metadata.version("posebusters"), implementation=implementation)


def verify_pose_endpoints(predicted_sdf, reference_sdf, receptor_pdb, *, check_contract,
                          qualification_refs, workspace, expected_pose_count,
                          expected_inputs, threshold=2.0):
    """Opt-in endpoint protocol; the legacy verifier and its verdicts are unchanged.

    ``qualification_refs`` maps zero-based slot strings to exact, released input
    qualification Artifact refs. Every supplied qualification is rechecked against
    original sources. Missing/failed qualification suppresses only the dependent
    RMSD and joint endpoints, never reference-free physical checks. All actual
    slots, malformed molecules, missing expected slots and errors are retained.

    This is a new local evaluation, not a new docking attempt or independent
    experimental validation. A historical unknown is never overwritten.
    """
    import copy
    import importlib.metadata
    from rdkit import Chem, rdBase
    import numpy as np
    from posebusters import PoseBusters
    from etalon.active.schema import digest
    from .result_integrity import validate_check_contract
    from .scientific_handoff import boundary_diagnostic, qualification_for_pose

    if type(expected_pose_count) is not int or not 1 <= expected_pose_count <= 100:
        raise ValueError("expected_pose_count must be 1..100")
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or threshold != 2.0:
        raise ValueError("endpoint protocol fixes the joint RMSD threshold at 2 Angstrom")
    if not isinstance(qualification_refs, dict) or any(
            not isinstance(k, str) or not k.isdecimal() or str(int(k)) != k
            or not 0 <= int(k) < expected_pose_count for k in qualification_refs):
        raise ValueError("qualification refs require expected zero-based slot string keys")
    check_contract = validate_check_contract(copy.deepcopy(check_contract))
    if installed_pose_check_contract(check_contract["configuration"]) != check_contract:
        raise ValueError("checker implementation/configuration changed")
    files = {"predicted": str(predicted_sdf), "reference": str(reference_sdf), "receptor": str(receptor_pdb)}
    pins = {key: identity(path) for key, path in files.items()}
    if pins != expected_inputs:
        raise ValueError("endpoint input identities differ from declared originals")
    if any(pin["bytes"] > 32 * 1024 * 1024 for pin in pins.values()):
        raise ValueError("endpoint input exceeds bounded file size")
    diagnostics, rows = [], []
    try:
        predictions = list(Chem.SDMolSupplier(files["predicted"], removeHs=False))
    except Exception as error:
        predictions = []
        diagnostics.append(boundary_diagnostic("prediction_parse", error))
    if len(predictions) > 100:
        raise ValueError("actual pose count exceeds 100; no silent truncation")
    try:
        references = list(Chem.SDMolSupplier(files["reference"], removeHs=False))
        if len(references) != 1 or references[0] is None:
            raise ValueError("one readable evaluator reference required")
        reference = references[0]
    except Exception as error:
        reference = None
        diagnostics.append(boundary_diagnostic("reference_parse", error))
    checker = PoseBusters(config=check_contract["configuration"])

    def unknown(reason):
        return {"status": "unknown", "value": None, "reason": reason}

    for index in range(max(expected_pose_count, len(predictions))):
        mol = predictions[index] if index < len(predictions) else None
        row = {"pose_index": index, "slot_present": index < len(predictions),
               "qualification": None, "physical": unknown("not_checked"),
               "rmsd": unknown("qualification_unavailable"),
               "joint": unknown("required_endpoints_unavailable"), "checks": {}, "diagnostics": []}
        ref = qualification_refs.get(str(index))
        if ref is not None:
            try:
                row["qualification"] = qualification_for_pose(ref, workspace=workspace,
                    predicted_identity=pins["predicted"], reference_identity=pins["reference"], pose_index=index)
            except Exception as error:
                row["diagnostics"].append(boundary_diagnostic("qualification", error, basis_refs=[ref]))
        else:
            row["diagnostics"].append(boundary_diagnostic("qualification",
                ValueError("no exact qualification supplied for this pose")))
        if mol is None:
            row["diagnostics"].append(boundary_diagnostic("prediction_parse",
                ValueError("unreadable predicted molecule" if row["slot_present"] else "missing expected pose slot")))
            row["physical"] = unknown("prediction_unreadable")
        else:
            try:
                before = mol.GetConformer().GetPositions().copy()
                frame = checker.bust(mol_pred=Chem.Mol(mol), mol_cond=Path(receptor_pdb), full_report=False)
                if len(frame) != 1:
                    raise ValueError("checker must return exactly one row per pose")
                checks = frame.iloc[0].to_dict()
                # Keep returned booleans even on failure, but never admit a subset.
                row["checks"] = {k: bool(v) for k, v in checks.items() if isinstance(v, (bool, np.bool_))}
                if (set(checks) != set(check_contract["required_checks"])
                        or len(row["checks"]) != len(checks)):
                    raise ValueError("missing, additional or nonboolean required physical checks")
                if not np.array_equal(before, mol.GetConformer().GetPositions()):
                    raise ValueError("physical checker changed prediction coordinates")
                row["physical"] = {"status": "observed", "value": all(row["checks"].values()),
                                   "reason": "all_frozen_physical_checks_observed"}
            except Exception as error:
                row["physical"] = unknown("physical_checker_failed")
                row["diagnostics"].append(boundary_diagnostic("physical", error))
            qualification = row["qualification"]
            if qualification is not None and qualification["eligibility"]["identity_comparable"]:
                try:
                    if reference is None:
                        raise ValueError("evaluator reference unreadable")
                    row["rmsd"] = {"status": "observed", "value": symmetry_rmsd(mol, reference),
                                   "reason": "fixed_frame_qualified_reference", "unit": "Angstrom"}
                except Exception as error:
                    row["rmsd"] = unknown("rmsd_failed")
                    row["diagnostics"].append(boundary_diagnostic("rmsd", error))
            elif qualification is not None:
                row["rmsd"] = unknown(qualification["eligibility"]["status"])
        if row["physical"]["status"] == row["rmsd"]["status"] == "observed":
            row["joint"] = {"status": "observed", "value": row["physical"]["value"] and row["rmsd"]["value"] <= threshold,
                            "reason": "all_frozen_checks_and_qualified_rmsd"}
        rows.append(row)
    if pins != {key: identity(path) for key, path in files.items()}:
        raise ValueError("endpoint input changed during evaluation")
    return {"schema": "etalon-pose-endpoint-verification/1", "inputs": pins,
            "expected_pose_count": expected_pose_count, "actual_pose_count": len(predictions),
            "slot_coverage_complete": len(predictions) == expected_pose_count,
            "threshold_A": threshold, "check_contract_digest": digest(check_contract),
            "qualification_refs": copy.deepcopy(qualification_refs), "rows": rows, "diagnostics": diagnostics,
            "posebusters_version": importlib.metadata.version("posebusters"),
            "parser": {"name": "RDKit", "version": rdBase.rdkitVersion, "module_path": Chem.__file__, "removeHs": False},
            "historical_results_modified": False, "scientific_truth": "not_certified",
            "scope": "new local evaluation of fixed originals; known-pocket fixed-receptor frame; no affinity or independent experimental claim"}
