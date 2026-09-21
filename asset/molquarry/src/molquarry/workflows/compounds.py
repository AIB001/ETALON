"""Chemistry properties and traceable 3D files; experimental poses retain heavy-atom positions."""

import hashlib
import json
import re
from pathlib import Path

from ..errors import MolQuarryError
from ..models import utcnow


def file_label(candidate_id):
    readable = re.sub(r"[^A-Za-z0-9_.-]", "_", candidate_id.split("#")[-1])[:50]
    return readable + "_" + hashlib.sha256(candidate_id.encode()).hexdigest()[:10]


def properties(smiles):
    from rdkit import Chem
    from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError("SMILES did not pass RDKit sanitization")
    return molecule, {
        "smiles_source": smiles,
        "smiles_rdkit": Chem.MolToSmiles(molecule, isomericSmiles=True),
        "inchikey": Chem.MolToInchiKey(molecule),
        "formula": rdMolDescriptors.CalcMolFormula(molecule),
        "molecular_weight": Descriptors.MolWt(molecule),
        "exact_mass": Descriptors.ExactMolWt(molecule),
        "logp_rdkit": Crippen.MolLogP(molecule),
        "tpsa": rdMolDescriptors.CalcTPSA(molecule),
        "hbd": Lipinski.NumHDonors(molecule),
        "hba": Lipinski.NumHAcceptors(molecule),
        "rotatable_bonds": Lipinski.NumRotatableBonds(molecule),
        "formal_charge": Chem.GetFormalCharge(molecule),
        "stereo_unspecified": sum(
            label == "?" for _, label in Chem.FindMolChiralCenters(molecule, includeUnassigned=True)
        ),
    }


def candidate_structure(candidate, dossier):
    """Only declared source identities or explicit structures qualify; no short-name lookup."""
    for identity in candidate.get("identities", []):
        source, identifier = identity.get("source"), str(identity.get("record_id", ""))
        if source == "rcsb":
            record = next(
                (r for r in dossier["ccd_ligands"] if r["chem_comp"]["id"] == identifier), None
            )
            if record:
                desc = record["rcsb_chem_comp_descriptor"]
                return desc.get("SMILES_stereo") or desc["SMILES"], desc["InChIKey"], identity
        if source == "chembl":
            record = next(
                (
                    r
                    for r in dossier["chembl_details"]["molecule"]
                    if r["molecule_chembl_id"] == identifier
                ),
                None,
            )
            if record and record.get("molecule_structures"):
                desc = record["molecule_structures"]
                return desc["canonical_smiles"], desc["standard_inchi_key"], identity
        if source == "pubchem":
            record = next(
                (r for r in dossier.get("pubchem", []) if str(r["CID"]) == identifier), None
            )
            if record:
                return record["SMILES"], record["InChIKey"], identity
        if identity.get("smiles"):
            return identity["smiles"], identity.get("inchikey"), identity
    return None, None, None


def prepare_molecule(molecule, *, experimental=False, seed=20260919):
    from rdkit import Chem
    from rdkit.Chem import AllChem

    Chem.SanitizeMol(molecule)
    original = Chem.RemoveHs(molecule)
    prepared = Chem.AddHs(original, addCoords=bool(original.GetNumConformers()))
    generated = not prepared.GetNumConformers() or not prepared.GetConformer().Is3D()
    if experimental and generated:
        raise ValueError("Experimental pose has no three-dimensional coordinates")
    if generated:
        options = AllChem.ETKDGv3()
        options.randomSeed = seed
        options.timeout = 10
        if AllChem.EmbedMolecule(prepared, options) != 0:
            raise ValueError("ETKDG could not produce a conformer")
    ff = None
    forcefield = None
    if AllChem.MMFFHasAllMoleculeParams(prepared):
        ff = AllChem.MMFFGetMoleculeForceField(
            prepared, AllChem.MMFFGetMoleculeProperties(prepared)
        )
        forcefield = "MMFF94"
    elif AllChem.UFFHasAllMoleculeParams(prepared):
        ff = AllChem.UFFGetMoleculeForceField(prepared)
        forcefield = "UFF"
    if ff is None:
        raise ValueError("No complete MMFF94/UFF parameter set; no corrected structure claimed")
    if experimental:
        for atom in prepared.GetAtoms():
            if atom.GetAtomicNum() != 1:
                ff.AddFixedPoint(atom.GetIdx())
    ff.Initialize()
    converged = ff.Minimize(maxIts=1000) == 0
    note = {
        "forcefield": forcefield,
        "converged": converged,
        "coordinates": "experimental_heavy_atoms_fixed"
        if experimental
        else "rdkit_etkdg_generated"
        if generated
        else "source_3d_minimized",
        "seed": seed if generated else None,
        "policy": "Sanitize, assign source chemistry, add H; retain salt/stereo policy from input.",
    }
    return prepared, note


def experimental_ligands(candidate, dossier, structure_root):
    import gemmi
    from rdkit import Chem
    from rdkit.Chem import AllChem

    smiles, _, _ = candidate_structure(candidate, dossier)
    ccds = [i["record_id"] for i in candidate.get("identities", []) if i.get("source") == "rcsb"]
    if not smiles or not ccds:
        return
    template = Chem.MolFromSmiles(smiles)
    for source in sorted((structure_root / "source_mmcif").glob("*.cif")):
        structure = gemmi.read_structure(str(source))
        for model_index, model in enumerate(structure):
            for chain in model:
                for residue in chain:
                    if residue.name not in ccds:
                        continue
                    # One copy per model/CCD is enough; preserve its chain/residue identity.
                    ligand = gemmi.Structure()
                    ligand.add_model(gemmi.Model(1))
                    ligand[0].add_chain(gemmi.Chain("A"))
                    ligand[0][0].add_residue(residue.clone())
                    ligand.remove_alternative_conformations()
                    mol = Chem.MolFromPDBBlock(
                        ligand.make_pdb_string(), sanitize=False, removeHs=True
                    )
                    if mol is None:
                        yield (
                            None,
                            {
                                "pdb_id": source.stem,
                                "error": "RDKit could not read the ligand pose",
                            },
                        )
                        continue
                    try:
                        mol = AllChem.AssignBondOrdersFromTemplate(template, mol)
                        if Chem.MolToInchiKey(Chem.RemoveHs(mol)) != Chem.MolToInchiKey(template):
                            raise ValueError(
                                "Repaired pose does not match source chemical identity"
                            )
                        yield (
                            mol,
                            {
                                "pdb_id": source.stem,
                                "model_index": model_index,
                                "auth_chain": chain.name,
                                "auth_residue": str(residue.seqid),
                                "ccd_id": residue.name,
                            },
                        )
                    except ValueError as exc:
                        yield None, {"pdb_id": source.stem, "error": str(exc)}


def export_compounds(
    quarry,
    dossier,
    reviewed,
    root,
    structure_root,
    *,
    purchasing=True,
    max_purchase_queries=20,
    generate_missing=True,
):
    from rdkit import Chem, rdBase

    root, structure_root = Path(root), Path(structure_root)
    for folder in ["source_3d", "sdf", "raw_queries"]:
        (root / folder).mkdir(parents=True, exist_ok=True)
    rows, purchases, sdf_index, queries = [], [], [], []
    purchase_calls = 0
    for candidate in reviewed["candidates"]:
        candidate_id = candidate["candidate_id"]
        label = file_label(candidate_id)
        row = {
            "candidate_id": candidate_id,
            "label": candidate["label"],
            "pmid": ";".join(
                sorted(
                    {
                        r["record_id"]
                        for r in candidate["references"]
                        if r["source"] == "europepmc" and not r["record_id"].startswith("PMC")
                    }
                )
            ),
            "activity_direction": candidate.get("activity_direction", "unknown"),
            "modality": candidate["modality"],
            "mechanism": candidate["mechanism"],
            "evidence_level": candidate["evidence_level"],
            "status": candidate["status"],
            "chemical_status": "unresolved",
            "notes": "; ".join(candidate["notes"]),
        }
        rows.append(row)
        smiles, expected_key, identity = candidate_structure(candidate, dossier)
        if not smiles:
            row.update(
                chemical_status="no_verified_structure",
                purchase_status="not_searched_identity_missing",
            )
            continue
        try:
            template, prop = properties(smiles)
            row.update(
                prop,
                identity_status=identity.get("status"),
                identity_source=identity.get("source"),
                identity_record=identity.get("record_id"),
                rdkit_version=rdBase.rdkitVersion,
            )
            if expected_key and prop["inchikey"] != expected_key:
                raise ValueError("RDKit identity differs from the source InChIKey")
            row["chemical_status"] = "source_identity_checked"
        except ValueError as exc:
            row.update(
                chemical_status="error",
                chemical_error=str(exc),
                purchase_status="not_searched_identity_error",
            )
            continue
        cid = None
        try:
            result = quarry.query(
                "pubchem", "properties", namespace="inchikey", identifier=row["inchikey"]
            )
            queries.append(result.model_dump())
            matches = [r for r in result.records if r.get("InChIKey") == row["inchikey"]]
            if matches:
                cid = matches[0]["CID"]
                row["pubchem_cid"] = cid
        except MolQuarryError as exc:
            row["pubchem_status"] = exc.code
        if purchasing and purchase_calls < max_purchase_queries:
            purchase_calls += 1
            try:
                result = quarry.query("mcule", "lookup", inchikey=row["inchikey"])
                queries.append(result.model_dump())
                row["purchase_status"] = (
                    "catalog_hit_stock_unverified" if result.records else "no_public_catalog_hit"
                )
                for match in result.records:
                    purchases.append(
                        {
                            "candidate_id": candidate_id,
                            "source": "mcule",
                            "retrieved_at": result.provenance[0].retrieved_at,
                            "status": "catalog_presence_only",
                            "stock": "unverified",
                            "price": None,
                            "currency": None,
                            "lead_time": None,
                            "record": match,
                        }
                    )
            except MolQuarryError as exc:
                row["purchase_status"] = exc.code
                purchases.append(
                    {
                        "candidate_id": candidate_id,
                        "source": "mcule",
                        "status": exc.code,
                        "retrieved_at": utcnow(),
                        "stock": "unknown",
                        "error": str(exc),
                    }
                )
        else:
            row["purchase_status"] = "not_searched_budget" if purchasing else "not_requested"
        if candidate["modality"] != "small_molecule" or template.GetNumHeavyAtoms() > 120:
            row["sdf_status"] = "not_prepared_modality_or_size"
            continue
        poses = []
        for molecule, info in experimental_ligands(candidate, dossier, structure_root):
            if molecule is None:
                sdf_index.append({"candidate_id": candidate_id, "status": "pose_error", **info})
            else:
                poses.append((molecule, {"origin": "experimental_complex", **info}))
        if not poses and cid:
            try:
                folder = root / "source_3d" / label
                plan = quarry.plan_download("pubchem", "sdf", cids=[cid], record_type="3d")
                download = quarry.download(plan, output_dir=folder, max_bytes=10_000_000)
                for mol in Chem.SDMolSupplier(download.path, removeHs=False):
                    if (
                        mol is not None
                        and Chem.MolToInchiKey(Chem.RemoveHs(mol)) == row["inchikey"]
                    ):
                        poses.append((mol, {"origin": "pubchem_computed_3d", "cid": cid}))
                if not poses:
                    row["source_3d_status"] = "no_matching_3d_molecule"
            except MolQuarryError as exc:
                row["source_3d_status"] = exc.code
        if not poses and generate_missing:
            poses = [(template, {"origin": "generated_from_source_smiles"})]
        files = []
        for number, (molecule, origin) in enumerate(poses, 1):
            try:
                prepared, processing = prepare_molecule(
                    molecule, experimental=origin["origin"] == "experimental_complex"
                )
                if Chem.MolToInchiKey(Chem.RemoveHs(prepared)) != row["inchikey"]:
                    raise ValueError("Preparation changed the source chemical identity")
                filename = f"{label}_{number:02d}.sdf"
                destination = root / "sdf" / filename
                prepared.SetProp("_Name", candidate_id)
                for key, value in {
                    "candidate_id": candidate_id,
                    "PMID": row["pmid"],
                    "InChIKey": row["inchikey"],
                    "provenance": json.dumps(origin),
                    "processing": json.dumps(processing),
                    "rdkit_version": rdBase.rdkitVersion,
                }.items():
                    prepared.SetProp(key, str(value))
                with destination.open("x", encoding="utf-8") as file, Chem.SDWriter(file) as writer:
                    writer.write(prepared)
                files.append(f"sdf/{filename}")
                sdf_index.append(
                    {
                        "candidate_id": candidate_id,
                        "file": files[-1],
                        "status": "written",
                        "origin": origin,
                        "processing": processing,
                    }
                )
            except (ValueError, RuntimeError) as exc:
                sdf_index.append(
                    {"candidate_id": candidate_id, "status": "preparation_error", "error": str(exc)}
                )
        row.update(sdf_files=";".join(files), sdf_status="written" if files else "unavailable")
    for index, result in enumerate(queries, 1):
        (root / "raw_queries" / f"{index:04d}.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    report = {
        "compounds": rows,
        "purchases": purchases,
        "sdf": sdf_index,
        "purchase_scope": (
            f"Public Mcule catalog lookup: {purchase_calls} calls, budget {max_purchase_queries}; "
            "no live prices, stock or orders."
            if purchasing
            else "Purchasing lookups were not requested."
        ),
        "rdkit_version": rdBase.rdkitVersion,
    }
    (root / "index.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
