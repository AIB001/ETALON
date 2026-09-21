"""Bounded, resumable computed conformers for database inventories."""

import hashlib
import json
import math
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

from .compounds import file_label, prepare_molecule
from .journal import digest


def _conformer(row):
    from rdkit import Chem

    result = {"compound_id": row["compound_id"], "origin": "computed_not_experimental"}
    try:
        molecule = Chem.MolFromSmiles(row["smiles_rdkit"])
        if molecule is None:
            raise ValueError("Invalid input SMILES")
        if molecule.GetNumHeavyAtoms() > 120:
            return {**result, "status": "not_prepared_size_budget"}
        prepared, processing = prepare_molecule(molecule)
        if Chem.MolToInchiKey(Chem.RemoveHs(prepared)) != row["inchikey"]:
            raise ValueError("Conformer identity differs from input")
        prepared.SetProp("_Name", row["compound_id"])
        return {
            **result,
            "status": "prepared",
            "processing": processing,
            "inchikey": row["inchikey"],
            "pmids": row["pmids"],
            "molblock": Chem.MolToMolBlock(prepared),
        }
    except (ValueError, RuntimeError) as exc:
        return {**result, "status": "preparation_error", "error": str(exc)}


def valid_conformer(molecule, inchikey):
    from rdkit import Chem

    return bool(
        molecule is not None
        and molecule.GetNumConformers()
        and molecule.GetConformer().Is3D()
        and Chem.MolToInchiKey(Chem.RemoveHs(molecule)) == inchikey
        and all(math.isfinite(v) for xyz in molecule.GetConformer().GetPositions() for v in xyz)
    )


def saved_result_valid(root, row):
    """A checkpoint is reusable only when its claimed file still passes chemistry checks."""
    from rdkit import Chem

    label = file_label(row["compound_id"])
    try:
        result = json.loads((root / f"{label}.json").read_text(encoding="utf-8"))
        if result["compound_id"] != row["compound_id"]:
            return False
        if result["status"] != "written":
            return result["status"] in {
                "preparation_error",
                "not_prepared_size_budget",
                "serialization_error",
            }
        if result["file"] != f"{label}.sdf":
            return False
        path = root / result["file"]
        if not path.is_file():
            return False
        if (
            result.get("sha256")
            and hashlib.sha256(path.read_bytes()).hexdigest() != result["sha256"]
        ):
            return False
        mol = next(iter(Chem.SDMolSupplier(str(path), removeHs=False)), None)
        return valid_conformer(mol, row["inchikey"])
    except (OSError, KeyError, ValueError, RuntimeError):
        return False


def export_inventory_conformers(inventory_path, output_dir, *, workers=2, progress=None):
    """Generate labeled 3D files, reusing only a matching immutable input request."""
    from rdkit import Chem, rdBase

    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    if Path(inventory_path).stat().st_size > 100_000_000:
        raise ValueError("Inventory exceeds the 100 MB budget")
    source = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    rows = [r for r in source["compounds"] if r["chemical_status"] == "parsed"]
    if len(rows) > 10000 or len({r["compound_id"] for r in rows}) != len(rows):
        raise ValueError("Conformer inventory requires unique IDs and at most 10,000 compounds")
    spec = {
        "identities": [
            {k: r[k] for k in ["compound_id", "smiles_rdkit", "inchikey", "pmids"]} for r in rows
        ],
        "rdkit": rdBase.rdkitVersion,
        "processing": "ETKDGv3 seed=20260919, timeout=10s; MMFF94/UFF, 1000 iterations",
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    request = root / "request.json"
    if request.exists():
        if json.loads(request.read_text()) != spec:
            raise ValueError("Conformer request differs from existing run")
    elif any(root.iterdir()):
        raise ValueError("Conformer directory already contains unrelated files")
    else:
        request.write_text(json.dumps(spec, indent=2) + "\n")
    pending = [r for r in rows if not saved_result_valid(root, r)]
    progress = progress or (lambda message: None)
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        for index, result in enumerate(pool.map(_conformer, pending), 1):
            label = file_label(result["compound_id"])
            if result["status"] == "prepared":
                molecule = Chem.MolFromMolBlock(result.pop("molblock"), removeHs=False)
                if not valid_conformer(molecule, result["inchikey"]):
                    result.update(status="serialization_error")
                else:
                    for key in ["origin", "inchikey", "pmids", "processing"]:
                        molecule.SetProp(key, json.dumps(result[key]))
                    temporary_sdf = root / f"{label}.pending.sdf"
                    with Chem.SDWriter(str(temporary_sdf)) as writer:
                        writer.write(molecule)
                    reread = next(iter(Chem.SDMolSupplier(str(temporary_sdf), removeHs=False)))
                    if valid_conformer(reread, result["inchikey"]):
                        sha = hashlib.sha256(temporary_sdf.read_bytes()).hexdigest()
                        temporary_sdf.replace(root / f"{label}.sdf")
                        result.update(status="written", file=f"{label}.sdf", sha256=sha)
                    else:
                        temporary_sdf.unlink()
                        result.update(status="serialization_error")
            record = root / f"{label}.json"
            temporary = record.with_suffix(".part")
            temporary.write_text(json.dumps(result, indent=2) + "\n")
            temporary.replace(record)
            if index % 25 == 0 or index == len(pending):
                progress(f"Computed conformers: {index}/{len(pending)}")
    results = [
        json.loads((root / f"{file_label(r['compound_id'])}.json").read_text()) for r in rows
    ]
    result = {
        "request_sha256": digest(spec),
        "records": results,
        "attempted": len(rows),
        "written": sum(r["status"] == "written" for r in results),
        "nonconverged": sum(r.get("processing", {}).get("converged") is False for r in results),
    }
    (root / "index.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
