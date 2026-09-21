"""Complete database evidence inventory; quantitative measurements are not curated claims."""

import json
import math
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .bundle import excel_workbook
from .compounds import properties
from .review import bindingdb_evidence_id


def measurement_class(endpoint, relation, value, unit, text=""):
    """Conservative evidence buckets, with no potency threshold or mechanism inference."""
    if re.search(r"\b(?:not active|inactive)\b", text, re.I):
        return "reported_inactive"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "nonquantitative_or_missing"
    if not math.isfinite(number) or number <= 0:
        return "other_readout_or_invalid_value"
    if endpoint not in {"Kd", "Ki", "IC50", "EC50"} or unit not in {
        "M",
        "mM",
        "uM",
        "µM",
        "μM",
        "nM",
        "pM",
    }:
        return "other_assay_readout"
    if relation in {">", ">="}:
        return "lower_bound_only"
    if relation in {"=", "<", "<="}:
        return "quantitative_potency_reported"
    return "relation_unresolved"


def ligand_inventory(dossier):
    """Join identities, citations and every collected measurement without losing negatives."""
    compounds, measurements, identity_errors = {}, [], []
    molecules = {r["molecule_chembl_id"]: r for r in dossier["chembl_details"]["molecule"]}
    documents = {r["document_chembl_id"]: r for r in dossier["chembl_details"]["document"]}
    descriptor_cache = {}

    def register(smiles, source, identifier, label=None, expected=None):
        if smiles not in descriptor_cache:
            try:
                descriptor_cache[smiles] = properties(smiles)[1] if smiles else None
            except (ValueError, RuntimeError):
                descriptor_cache[smiles] = None
        prop = descriptor_cache[smiles]
        key = prop["inchikey"] if prop and prop.get("inchikey") else f"{source}:{identifier}"
        if expected and prop and key != expected:
            identity_errors.append(
                {
                    "source": source,
                    "record_id": identifier,
                    "source_inchikey": expected,
                    "rdkit_inchikey": key,
                }
            )
            # Preserve disputed source records separately until a reviewer resolves identity.
            key = f"{source}:{identifier}:identity_conflict"
        if key not in compounds:
            compounds[key] = {
                "compound_id": key,
                **(prop or {}),
                "names": set(),
                "source_ids": set(),
                "pmids": set(),
                "dois": set(),
                "measurement_classes": Counter(),
                "measurement_count": 0,
                "chemical_status": "identity_conflict"
                if ":identity_conflict" in key
                else "parsed"
                if prop
                else "structure_unavailable",
            }
        compounds[key]["source_ids"].add(f"{source}:{identifier}")
        if label:
            compounds[key]["names"].add(label)
        return key

    seen = set()
    for item in [*dossier["activities"], *dossier.get("assay_mention_activities", [])]:
        raw = item["record"]
        identity = (raw["activity_id"], item.get("input_target"))
        if identity in seen:
            continue
        seen.add(identity)
        identifier = raw["molecule_chembl_id"]
        molecule = molecules.get(identifier, {})
        structure = molecule.get("molecule_structures") or {}
        key = register(
            structure.get("canonical_smiles") or raw.get("canonical_smiles"),
            "chembl",
            identifier,
            raw.get("molecule_pref_name"),
            structure.get("standard_inchi_key"),
        )
        document = documents.get(raw.get("document_chembl_id"), {})
        measurements.append(
            {
                "compound_id": key,
                "source": "chembl",
                "record_id": str(raw["activity_id"]),
                "source_compound_id": identifier,
                "target": item.get("input_target"),
                "target_source_id": raw.get("target_chembl_id"),
                "target_organism": raw.get("target_organism"),
                "target_assignment": item.get("review_status"),
                "endpoint": raw.get("standard_type"),
                "relation": raw.get("standard_relation"),
                "value": raw.get("standard_value"),
                "unit": raw.get("standard_units"),
                "text_value": raw.get("standard_text_value") or raw.get("text_value"),
                "assay_id": raw.get("assay_chembl_id"),
                "assay_type": raw.get("assay_type"),
                "assay_description": raw.get("assay_description"),
                "variant": raw.get("assay_variant_mutation"),
                "pmid": str(document.get("pubmed_id") or ""),
                "doi": document.get("doi"),
                "document_id": raw.get("document_chembl_id"),
                "potential_duplicate": raw.get("potential_duplicate"),
                "data_validity_comment": raw.get("data_validity_comment"),
                "activity_comment": raw.get("activity_comment"),
                "raw": raw,
            }
        )
    for item in dossier["bindingdb"]:
        raw = item["record"]
        key = register(raw.get("smile"), "bindingdb", raw["monomerid"])
        match = re.fullmatch(
            r"\s*(<=|>=|<|>|=|~)?\s*([+-]?[\d.]+(?:[eE][+-]?\d+)?)\s*", str(raw.get("affinity", ""))
        )
        measurements.append(
            {
                "compound_id": key,
                "source": "bindingdb",
                "record_id": bindingdb_evidence_id(raw),
                "source_compound_id": raw["monomerid"],
                "target": item.get("input_target"),
                "target_assignment": "accession_query; construct and species detail not supplied",
                "endpoint": raw.get("affinity_type"),
                "relation": (match.group(1) or "=") if match else None,
                "value": match.group(2) if match else None,
                "unit": "nM",
                "unit_basis": "BindingDB REST affinity cutoff/value convention; see raw record",
                "text_value": None if match else raw.get("affinity"),
                "pmid": str(raw.get("pmid") or ""),
                "doi": raw.get("doi"),
                "raw": raw,
            }
        )
    overlap = defaultdict(list)
    for row in measurements:
        row["evidence_class"] = measurement_class(
            row["endpoint"],
            row["relation"],
            row["value"],
            row["unit"],
            " ".join(str(row.get(k) or "") for k in ["text_value", "activity_comment"]),
        )
        compound = compounds[row["compound_id"]]
        compound["measurement_classes"][row["evidence_class"]] += 1
        compound["measurement_count"] += 1
        if row["pmid"]:
            compound["pmids"].add(row["pmid"])
        if row["doi"]:
            compound["dois"].add(row["doi"])
        # An overlap lead, not a claim that these are the same independent experiment.
        try:
            numeric_value = str(Decimal(str(row["value"])).normalize())
        except InvalidOperation:
            numeric_value = str(row["value"])
        overlap[
            (
                row["compound_id"],
                row["target"],
                row["pmid"],
                row["endpoint"],
                row["relation"],
                numeric_value,
                row["unit"],
            )
        ].append(row)
    for group in overlap.values():
        for row in group:
            row["possible_cross_source_overlap"] = bool(
                row["pmid"] and len({r["source"] for r in group}) > 1
            )
    for compound in compounds.values():
        for field in ["names", "source_ids", "pmids", "dois"]:
            compound[field] = sorted(compound[field])
        compound["has_reported_quantitative_potency"] = bool(
            compound["measurement_classes"]["quantitative_potency_reported"]
        )
        compound["interpretation"] = (
            "Database evidence; direction/selectivity requires assay review"
        )
        compound["purchase_status"] = "not_queried"
    return {
        "compounds": list(compounds.values()),
        "measurements": measurements,
        "identity_errors": identity_errors,
    }


def export_ligand_inventory(
    dossier_path, output_dir, *, sourcing_path=None, highlights=None, conformer_index_path=None
):
    """Write all tested compounds, evidence buckets, original assays and a 2D identity SDF."""
    from rdkit import Chem
    from rdkit.Chem import rdDepictor

    dossier = json.loads(Path(dossier_path).read_text(encoding="utf-8"))
    result = ligand_inventory(dossier)
    root = Path(output_dir)
    purchases = {}
    sourcing = json.loads(Path(sourcing_path).read_text(encoding="utf-8")) if sourcing_path else {}
    conformers = (
        json.loads(Path(conformer_index_path).read_text(encoding="utf-8"))["records"]
        if conformer_index_path
        else []
    )
    conformer_map = {r["compound_id"]: r for r in conformers}
    for row in sourcing.get("identities", []):
        purchases[row["inchikey"]] = row
    root.mkdir(parents=True, exist_ok=False)
    sdf_index = []
    with Chem.SDWriter(str(root / "all_tested_compounds_2d.sdf")) as writer:
        for row in result["compounds"]:
            conformer = conformer_map.get(row["compound_id"], {})
            row["computed_3d_status"] = conformer.get("status", "not_requested")
            row["computed_3d_file"] = conformer.get("file")
            row["computed_3d_error"] = conformer.get("error")
            offer = purchases.get(row.get("inchikey"), {})
            row.update(
                {
                    k: offer[k]
                    for k in ["purchase_status", "vendor_listing_count", "can_purchase_now"]
                    if k in offer
                }
            )
            if row["chemical_status"] != "parsed":
                continue
            molecule = Chem.MolFromSmiles(row["smiles_rdkit"])
            rdDepictor.Compute2DCoords(molecule)
            molecule.SetProp("_Name", row["compound_id"])
            for key in ["inchikey", "source_ids", "pmids", "has_reported_quantitative_potency"]:
                molecule.SetProp(
                    key, json.dumps(row[key]) if not isinstance(row[key], str) else row[key]
                )
            molecule.SetProp("coordinate_origin", "RDKit depiction; 2D identity record")
            writer.write(molecule)
            sdf_index.append({"compound_id": row["compound_id"], "coordinates": "2D depiction"})
    limitations = [
        "All collected tested compounds are retained, including inactive and single-dose hits.",
        "Quantitative potency is a data bucket; mechanism and selectivity still need review.",
        "BindingDB is cutoff-filtered and may overlap ChEMBL; original measurements are retained.",
        "Assay-mention hits need target assignment review. Source validity flags are preserved.",
        "A literature cutoff does not make current database snapshots historical snapshots.",
        "No claim of exhaustive patents, supplements, unpublished compounds or inaccessible text.",
        "The inventory SDF has 2D coordinates; experimental/computed 3D belongs in separate files.",
    ]
    excel_workbook(
        root / "ligands.xlsx",
        {
            "Compounds": result["compounds"],
            "QuantitativePotency": [
                r for r in result["compounds"] if r["has_reported_quantitative_potency"]
            ],
            "Measurements": result["measurements"],
            "LiteratureHighlights": highlights or [],
            "VendorListings": sourcing.get("vendors", []),
            "IdentityErrors": result["identity_errors"],
            "Computed3D": conformers,
            "Coverage": dossier["coverage"],
            "Papers": [
                {k: p.get(k) for k in ["id", "pmcid", "title", "doi", "firstPublicationDate"]}
                for p in dossier["papers"]
            ],
            "Limitations": [{"limitation": s} for s in limitations],
        },
    )
    result.update(limitations=limitations, sdf_index=sdf_index)
    (root / "inventory.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return {
        "workbook": str(root / "ligands.xlsx"),
        "compounds": len(result["compounds"]),
        "measurements": len(result["measurements"]),
        "sdf_records": len(sdf_index),
        "with_quantitative_potency": sum(
            r["has_reported_quantitative_potency"] for r in result["compounds"]
        ),
        "identity_conflicts": len(result["identity_errors"]),
    }
