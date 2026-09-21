"""SDF-to-Excel identity, catalog and synthesis-evidence screening through MolQuarry."""

import csv
import hashlib
import json
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import Field

from .._version import __version__
from ..models import InputModel, utcnow
from .bundle import default_output, excel_workbook
from .compounds import properties
from .journal import QueryJournal, digest


class SourcingConfig(InputModel):
    workers: int = Field(default=4, ge=1, le=4)
    max_records: int = Field(default=10000, ge=1, le=100000)
    max_input_bytes: int = Field(default=100_000_000, ge=1, le=1_000_000_000)
    pubchem: bool = True
    chembl: bool = True
    unichem: bool = True
    max_mcule_queries: int = Field(default=20, ge=0, le=50)
    include_parent_variants: bool = True
    local_catalogs: list[str] = Field(default_factory=list, max_length=20)


def read_sdf(path, config):
    """Preserve record order, failures and original fields; do not silently strip stereo/salts."""
    from rdkit import Chem

    path = Path(path)
    if path.stat().st_size > config.max_input_bytes:
        raise ValueError("Input SDF exceeds the configured size budget")
    rows, structures = [], {}
    for index, mol in enumerate(Chem.SDMolSupplier(str(path), removeHs=False), 1):
        if index > config.max_records:
            raise ValueError(
                "SDF exceeds the configured record count; no truncated result exported"
            )
        row = {"record_number": index, "input_name": "", "parse_status": "invalid_sdf_record"}
        rows.append(row)
        if mol is None:
            continue
        row.update(
            input_name=mol.GetProp("_Name") if mol.HasProp("_Name") else "",
            input_properties={key: mol.GetProp(key) for key in mol.GetPropNames()},
        )
        heavy = Chem.RemoveHs(mol)
        _, descriptors = properties(Chem.MolToSmiles(heavy, isomericSmiles=True))
        key = descriptors["inchikey"]
        if not key:
            row["parse_status"] = "identity_unavailable"
            continue
        row.update(
            descriptors,
            parse_status="parsed",
            identity_policy="SDF stereo/isotope/charge preserved",
        )
        row["parent_id"] = row["input_properties"].get("MOLCASCADE_PARENT_ID")
        row["docking_score"] = row["input_properties"].get("MOLCASCADE_DOCKING_SCORE")
        structures.setdefault(
            key, {"inchikey": key, "smiles": descriptors["smiles_rdkit"], "role": "sdf"}
        )
        if structures[key]["role"] == "parent_variant":
            structures[key]["role"] = "sdf_and_parent_variant"
        raw_parent = row["input_properties"].get("MOLCASCADE_PARENT_SMILES")
        if raw_parent:
            parent = Chem.MolFromSmiles(raw_parent)
            if parent is None:
                row["parent_identity_status"] = "invalid_parent_smiles"
            else:
                parent_key = Chem.MolToInchiKey(parent)
                row["parent_inchikey"] = parent_key
                row["parent_smiles"] = Chem.MolToSmiles(parent, isomericSmiles=True)
                row["parent_identity_status"] = (
                    "exact"
                    if key == parent_key
                    else "stereo_or_other_layer_differs"
                    if key.split("-")[0] == parent_key.split("-")[0]
                    else "connectivity_differs"
                )
                if config.include_parent_variants and parent_key and parent_key != key:
                    if parent_key in structures and structures[parent_key]["role"] == "sdf":
                        structures[parent_key]["role"] = "sdf_and_parent_variant"
                    structures.setdefault(
                        parent_key,
                        {
                            "inchikey": parent_key,
                            "smiles": row["parent_smiles"],
                            "role": "parent_variant",
                        },
                    )
    if not rows:
        raise ValueError("SDF contains no records")
    return rows, structures


def synthesis_descriptor(smiles):
    """SA score is a structural heuristic, never a route or a yes/no synthesis verdict."""
    from rdkit import Chem
    from rdkit.Chem import BRICS
    from rdkit.Contrib.SA_Score import sascorer

    molecule = Chem.MolFromSmiles(smiles)
    return {
        "sa_score": float(sascorer.calculateScore(molecule)),
        "sa_scale": "1 easier to 10 harder; heuristic only",
        "brics_cut_count": len(list(BRICS.FindBRICSBonds(molecule))),
        "synthesis_status": "not_established",
        "route_status": "no_validated_route_retrieved",
        "make_on_demand_status": "not_queried_requires_supplier_access",
    }


def result_rows(record):
    return record.get("result", {}).get("records", [])


def lookup_batches(journal, source, operation, keys, size, progress):
    found, statuses = {}, {}
    for start in range(0, len(keys), size):
        batch = keys[start : start + size]
        parameters = {"inchikeys": batch}
        if source == "chembl":
            parameters["limit"] = 100
        response = journal.query(source, operation, **parameters)
        visited = {digest(parameters)}
        page_count = 1
        for key in batch:
            statuses[key] = {
                "status": "no_exact_match_in_source"
                if response["status"] in {"ok", "not_found"}
                else response["status"],
                "query_id": response["query_id"],
            }
        while True:
            for row in result_rows(response):
                key = row.get("InChIKey") or (row.get("molecule_structures") or {}).get(
                    "standard_inchi_key"
                )
                if key in batch:
                    found.setdefault(key, []).append(row)
                    statuses[key]["status"] = "exact_match"
            next_params = response.get("result", {}).get("next_parameters")
            if not next_params:
                break
            if digest(next_params) in visited or page_count >= 100:
                for key in batch:
                    statuses[key]["status"] = "partial_pagination"
                break
            visited.add(digest(next_params))
            page_count += 1
            response = journal.query(source, operation, **next_params)
            if response["status"] != "ok":
                for key in batch:
                    statuses[key]["status"] = "partial_error"
                break
        progress(f"{source}: {min(start + size, len(keys))}/{len(keys)} identity keys")
    return found, statuses


def local_catalog_matches(paths, structures):
    """Exact matches from user-selected, authorized CSV/TSV/SDF snapshots."""
    from rdkit import Chem

    matches, coverage = {}, []
    for value in paths:
        path = Path(value).resolve()
        if path.stat().st_size > 100_000_000:
            raise ValueError("Local catalog exceeds 100 MB; provide a bounded snapshot")
        if path.suffix.lower() == ".sdf":
            records = (
                (
                    {**mol.GetPropsAsDict(), "smiles": Chem.MolToSmiles(Chem.RemoveHs(mol))}
                    if mol
                    else {}
                )
                for mol in Chem.SDMolSupplier(str(path), removeHs=False)
            )
            file = None
        elif path.suffix.lower() in {".csv", ".tsv"}:
            file = path.open(encoding="utf-8-sig", newline="")
            records = csv.DictReader(file, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
            fields = records.fieldnames
            if (
                not fields
                or any(not f for f in fields)
                or len({f.casefold() for f in fields}) != len(fields)
            ):
                file.close()
                raise ValueError("Local catalog requires unique, nonempty column names")
        else:
            raise ValueError("Local catalog must be CSV, TSV or SDF")
        seen, invalid = 0, 0
        try:
            for record in records:
                seen += 1
                if seen > 100000:
                    raise ValueError("Local catalog exceeds 100,000 records")
                if (
                    None in record
                    or any(v is None for v in record.values())
                    or len({k.casefold() for k in record}) != len(record)
                ):
                    raise ValueError("Local catalog has a malformed row or ambiguous columns")
                lower = {key.lower(): val for key, val in record.items()}
                smiles = (
                    lower.get("smiles")
                    or lower.get("canonical_smiles")
                    or lower.get("isomeric_smiles")
                )
                molecule = Chem.MolFromSmiles(str(smiles)) if smiles else None
                if molecule is None:
                    invalid += 1
                    continue
                key = Chem.MolToInchiKey(molecule)
                if key in structures:
                    matches.setdefault(key, []).append(
                        {"source": "local_catalog", "catalog": path.name, "record": record}
                    )
        finally:
            if file:
                file.close()
        coverage.append(
            {
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rows": seen,
                "unparsed": invalid,
                "scope": "exact source structure; stock fields are snapshot claims only",
            }
        )
    return matches, coverage


def screen_sdf(
    quarry,
    input_sdf,
    output_dir=None,
    *,
    config=None,
    resume=False,
    retry_errors=False,
    progress=None,
):
    """Run real public lookups for every unique identity and retain resumable request evidence."""
    import openpyxl  # noqa: F401 -- fail before queries when an export dependency is missing
    from rdkit import Chem, rdBase
    from rdkit.Contrib.SA_Score import sascorer  # noqa: F401

    config = config or SourcingConfig()
    if isinstance(config, dict):
        config = SourcingConfig.model_validate(config)
    progress = progress or (lambda message: None)
    source = Path(input_sdf).resolve()
    rows, structures = read_sdf(source, config)
    input_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    local, local_coverage = local_catalog_matches(config.local_catalogs, structures)
    spec = {
        "input_sha256": input_hash,
        "config": config.model_dump(),
        "local_catalogs": local_coverage,
    }
    root = (
        Path(output_dir).resolve()
        if output_dir
        else default_output([{"input": source.stem}], "sourcing")
    )
    if root.exists():
        if not resume:
            raise FileExistsError(root)
        previous = json.loads((root / "request.json").read_text(encoding="utf-8"))
        if previous != spec:
            raise ValueError("Resume input/config/catalog fingerprint differs from the saved run")
        copied_input = root / "input" / "shortlist.sdf"
        if (
            not copied_input.is_file()
            or hashlib.sha256(copied_input.read_bytes()).hexdigest() != input_hash
        ):
            raise ValueError("Saved input copy differs from the original run")
    else:
        root.mkdir(parents=True)
        (root / "input").mkdir()
        shutil.copyfile(source, root / "input" / "shortlist.sdf")
        (root / "request.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    journal = QueryJournal(quarry, root / "evidence" / "queries", retry_errors=retry_errors)
    keys = sorted(structures)
    pubchem, pc_status = (
        lookup_batches(journal, "pubchem", "batch_properties", keys, 100, progress)
        if config.pubchem
        else ({}, {})
    )
    chembl, ch_status = (
        lookup_batches(journal, "chembl", "molecules_by_inchikey", keys, 50, progress)
        if config.chembl
        else ({}, {})
    )
    mappings, uc_status, vendor_rows, library_rows = {}, {}, [], []
    source_inventory = journal.query("unichem", "sources") if config.unichem else None
    if config.unichem:

        def lookup(key):
            return key, journal.query("unichem", "mapping", compound=key, type="inchikey")

        with ThreadPoolExecutor(max_workers=config.workers) as pool:
            for count, (key, response) in enumerate(pool.map(lookup, keys), 1):
                verified = []
                for record in result_rows(response):
                    inchi = (record.get("inchi") or {}).get("inchi")
                    if inchi and Chem.InchiToInchiKey(inchi) == key:
                        verified.extend(record.get("sources", []))
                mappings[key] = verified
                uc_status[key] = {
                    "status": "exact_match"
                    if verified
                    else "no_exact_match_in_source"
                    if response["status"] in {"ok", "not_found"}
                    else response["status"],
                    "query_id": response["query_id"],
                }
                if count % 50 == 0 or count == len(keys):
                    progress(f"unichem: {count}/{len(keys)} identity keys")
    cids = sorted({record["CID"] for values in pubchem.values() for record in values})
    categories = {}
    for cid in cids:
        categories[cid] = journal.query("pubchem", "source_categories", cid=cid)
    mcule, mcule_status = {}, {}
    # Prefer primary SDF identities. This explicit cap is below anonymous daily quota.
    selected = sorted({r["inchikey"] for r in rows if r.get("inchikey")})[
        : config.max_mcule_queries
    ]
    for index, key in enumerate(selected, 1):
        response = journal.query("mcule", "lookup", inchikey=key)
        mcule[key] = result_rows(response)
        mcule_status[key] = {
            "status": "catalog_match"
            if result_rows(response)
            else "no_exact_match_in_source"
            if response["status"] in {"ok", "not_found"}
            else response["status"],
            "query_id": response["query_id"],
        }
        progress(f"mcule: {index}/{len(selected)} budgeted lookups")
        if response["status"] in {"rate_limited", "access_denied", "authentication_required"}:
            break
    identity_rows = []
    by_key = {}
    for key in keys:
        library_start, vendor_start = len(library_rows), len(vendor_rows)
        evidence = {
            "inchikey": key,
            "smiles": structures[key]["smiles"],
            "role": structures[key]["role"],
            "pubchem_status": pc_status.get(key, {}).get("status", "not_requested"),
            "chembl_status": ch_status.get(key, {}).get("status", "not_requested"),
            "unichem_status": uc_status.get(key, {}).get("status", "not_requested"),
            "mcule_status": mcule_status.get(key, {}).get(
                "status", "not_queried_budget" if config.max_mcule_queries else "not_requested"
            ),
        }
        identity_rows.append(evidence)
        by_key[key] = evidence
        for item in pubchem.get(key, []):
            library_rows.append(
                {
                    "inchikey": key,
                    "source": "pubchem",
                    "record_id": str(item["CID"]),
                    "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{item['CID']}",
                    "match": "full_inchikey",
                    "query_id": pc_status[key]["query_id"],
                }
            )
            category_response = categories[item["CID"]]
            evidence["vendor_lookup_status"] = category_response["status"]
            for group in result_rows(category_response):
                if group.get("Category") != "Chemical Vendors":
                    continue
                for vendor in group.get("Sources", []):
                    vendor_rows.append(
                        {
                            "inchikey": key,
                            "source": "pubchem_vendor_deposition",
                            "supplier": vendor.get("SourceName"),
                            "catalog_id": vendor.get("RegistryID"),
                            "sid": vendor.get("SID"),
                            "cid": item["CID"],
                            "url": vendor.get("SourceRecordURL"),
                            "status": "catalog_listing_stock_unverified",
                            "stock": "unverified",
                            "price": None,
                            "currency": None,
                            "lead_time": None,
                            "checked_at": category_response["checked_at"],
                            "query_id": category_response["query_id"],
                        }
                    )
        for item in chembl.get(key, []):
            library_rows.append(
                {
                    "inchikey": key,
                    "source": "chembl",
                    "record_id": item["molecule_chembl_id"],
                    "url": f"https://www.ebi.ac.uk/chembl/compound_report_card/{item['molecule_chembl_id']}/",
                    "match": "full_inchikey",
                    "query_id": ch_status[key]["query_id"],
                }
            )
        for item in mappings.get(key, []):
            library_rows.append(
                {
                    "inchikey": key,
                    "source": item.get("shortName"),
                    "record_id": item.get("compoundId"),
                    "url": item.get("url"),
                    "match": "full_inchi_via_unichem",
                    "query_id": uc_status[key]["query_id"],
                }
            )
            if item.get("shortName") in {"molport", "mcule", "emolecules", "enamine"}:
                vendor_rows.append(
                    {
                        "inchikey": key,
                        "source": "unichem_supplier_mapping",
                        "supplier": item["shortName"],
                        "catalog_id": item.get("compoundId"),
                        "url": item.get("url"),
                        "status": "catalog_cross_reference_stock_unverified",
                        "stock": "unverified",
                        "query_id": uc_status[key]["query_id"],
                    }
                )
        for item in mcule.get(key, []):
            library_rows.append(
                {
                    "inchikey": key,
                    "source": "mcule",
                    "record": item,
                    "match": "full_inchikey_lookup",
                    "query_id": mcule_status[key]["query_id"],
                }
            )
            vendor_rows.append(
                {
                    "inchikey": key,
                    "source": "mcule",
                    "supplier": "Mcule",
                    "record": item,
                    "status": "catalog_listing_stock_unverified",
                    "stock": "unverified",
                    "query_id": mcule_status[key]["query_id"],
                }
            )
        for item in local.get(key, []):
            library_rows.append(
                {"inchikey": key, "source": "local_catalog", "match": "full_inchikey", **item}
            )
        evidence.update(synthesis_descriptor(structures[key]["smiles"]))
        evidence["library_match_count"] = len(library_rows) - library_start
        evidence["vendor_listing_count"] = len(vendor_rows) - vendor_start
        evidence["purchase_status"] = (
            "catalog_listed_stock_unverified"
            if evidence["vendor_listing_count"]
            else "not_confirmed_in_queried_public_sources"
        )
        evidence["can_purchase_now"] = "unverified"
        evidence["can_be_synthesized"] = "not_established"
        evidence["existence_status"] = (
            "found_in_queried_libraries"
            if evidence["library_match_count"]
            else "not_found_in_completed_queries"
        )
        if not evidence["library_match_count"] and any(
            evidence[f"{s}_status"] not in {"no_exact_match_in_source", "not_requested"}
            for s in ["pubchem", "chembl", "unichem"]
        ):
            evidence["existence_status"] = "incomplete_lookup"
        if not any(
            [
                config.pubchem,
                config.chembl,
                config.unichem,
                config.local_catalogs,
                key in mcule_status,
            ]
        ):
            evidence["existence_status"] = "not_searched"
    for row in rows:
        key = row.get("inchikey")
        if key:
            row.update(
                {k: value for k, value in by_key[key].items() if k not in {"smiles", "role"}}
            )
            parent_key = row.get("parent_inchikey")
            if parent_key and parent_key != key and parent_key in by_key:
                alternate = by_key[parent_key]
                row.update(
                    parent_library_match_count=alternate["library_match_count"],
                    parent_vendor_listing_count=alternate["vendor_listing_count"],
                    parent_matches_are_exact_sdf=False,
                )
    evidence_records = journal.records()
    coverage = [
        {
            "source": r["request"]["source"],
            "operation": r["request"]["operation"],
            "query_id": r["query_id"],
            "status": r["status"],
            "returned": r.get("result", {}).get("returned"),
            "checked_at": r["checked_at"],
            "parameters": r["request"]["parameters"],
        }
        for r in evidence_records
    ]
    limitations = [
        "No orders or quotes submitted; catalog matches do not confirm current stock or prices.",
        "Absence applies only to completed exact-identity searches, "
        "not to every chemical library or patent.",
        "Parent-SMILES identity variants are queried separately; they are not exact SDF matches.",
        "SA score and BRICS cuts are heuristics; no validated route or synthesis is established.",
        "Enamine REAL, Chemspace, MolPort live sourcing and credentialed APIs were not queried.",
        f"Mcule lookup budget: {config.max_mcule_queries}; other structures are unqueried there.",
    ]
    sheets = {
        "Compounds": rows,
        "IdentityVariants": identity_rows,
        "LibraryMatches": library_rows,
        "VendorListings": vendor_rows,
        "SynthesisAssessment": [
            {
                k: r[k]
                for k in [
                    "inchikey",
                    "smiles",
                    "sa_score",
                    "sa_scale",
                    "brics_cut_count",
                    "synthesis_status",
                    "route_status",
                    "make_on_demand_status",
                ]
            }
            for r in identity_rows
        ],
        "QueryCoverage": coverage,
        "LocalCatalogs": local_coverage,
        "UniChemSources": result_rows(source_inventory) if source_inventory else [],
        "Limitations": [{"limitation": value} for value in limitations],
    }
    tables = root / "tables"
    tables.mkdir(exist_ok=True)
    pending = tables / "sourcing.pending.xlsx"
    if pending.exists():
        pending.unlink()
    excel_workbook(pending, sheets)
    pending.replace(tables / "sourcing.xlsx")
    (root / "results.json").write_text(
        json.dumps(
            {
                "compounds": rows,
                "identities": identity_rows,
                "library_matches": library_rows,
                "vendors": vendor_rows,
                "coverage": coverage,
                "limitations": limitations,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result = {
        "status": "completed_with_declared_scope",
        "created_at": utcnow(),
        "output_directory": str(root),
        "workbook": "tables/sourcing.xlsx",
        "input_sha256": input_hash,
        "input_records": len(rows),
        "parsed_records": sum(r["parse_status"] == "parsed" for r in rows),
        "unique_sdf_identities": len({r["inchikey"] for r in rows if r.get("inchikey")}),
        "queried_identity_variants": len(keys),
        "records_with_identity_disagreement": sum(
            r.get("parent_identity_status") not in {None, "exact"} for r in rows
        ),
        "records_with_library_matches": sum(bool(r.get("library_match_count")) for r in rows),
        "records_with_vendor_listings": sum(bool(r.get("vendor_listing_count")) for r in rows),
        "confirmed_in_stock": 0,
        "validated_synthesis_routes": 0,
        "query_statuses": dict(Counter(r["status"] for r in coverage)),
        "versions": {"molquarry": __version__, "rdkit": rdBase.rdkitVersion},
        "limitations": limitations,
    }
    (root / "run.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# MolQuarry sourcing screen\n\nOpen `tables/sourcing.xlsx`. Each SDF record retains its "
        "row number, original properties, exact identity and source-specific query status. "
        "`IdentityVariants` distinguishes SDF stereochemistry from parent-SMILES variants. "
        "`LibraryMatches` and `VendorListings` contain source records and links. "
        "`SynthesisAssessment` is a heuristic, not route validation. "
        "`evidence/queries/` holds resumable public query responses and errors.\n\n"
        + "\n".join(f"- {s}" for s in limitations)
        + "\n",
        encoding="utf-8",
    )
    manifest = [
        {
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    (root / "manifest.json").write_text(
        json.dumps(
            {"created_at": utcnow(), "files": manifest, "request_fingerprint": digest(spec)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result
