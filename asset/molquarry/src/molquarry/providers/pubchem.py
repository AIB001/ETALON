from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import Field

from ..models import InputModel
from .base import Operation, Page, Provider

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PROPERTIES = (
    "MolecularFormula,MolecularWeight,SMILES,ConnectivitySMILES,InChI,InChIKey,"
    "XLogP,TPSA,HBondDonorCount,HBondAcceptorCount"
)


class CompoundInput(InputModel):
    identifier: str = Field(min_length=1, max_length=4000)
    namespace: Literal["name", "cid", "inchikey", "smiles"] = "name"


class SimilarityInput(InputModel):
    smiles: str = Field(min_length=1, max_length=4000)
    threshold: int = Field(default=90, ge=0, le=100)
    limit: int = Field(default=20, ge=1, le=100)


class SubstructureInput(InputModel):
    smiles: str = Field(min_length=1, max_length=4000)
    stereo: Literal["ignore", "exact", "relative", "nonconflicting"] = "exact"
    match_charges: bool = True
    match_isotopes: bool = True
    rings_not_embedded: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class AssayInput(InputModel):
    aid: int = Field(gt=0)


class CIDInput(InputModel):
    cid: int = Field(gt=0)


class BatchProperties(InputModel):
    inchikeys: list[Annotated[str, Field(pattern=r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")]] = Field(
        min_length=1, max_length=100
    )


class SDFInput(InputModel):
    cids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=100)
    record_type: Literal["2d", "3d"] = "2d"


class PubChem(Provider):
    id = "pubchem"
    download_hosts = frozenset({"pubchem.ncbi.nlm.nih.gov"})
    operations = {
        "batch_properties": Operation(
            "Exact full InChIKey properties for up to 100 structures. Partial matches are "
            "returned by identity, not input order; an all-missing batch can return HTTP 404.",
            BatchProperties,
            {"inchikeys": ["BSYNRYMUTXBXSQ-UHFFFAOYSA-N"]},
        ),
        "source_categories": Operation(
            "PubChem substance depositors grouped by category, including Chemical Vendors. "
            "Depositor catalog links are not current stock or quotes.",
            CIDInput,
            {"cid": 2244},
        ),
        "properties": Operation(
            "Resolve a compound and retrieve identity/properties; SMILES uses POST.",
            CompoundInput,
            {"identifier": "aspirin"},
        ),
        "similarity": Operation(
            "PubChem 2D fingerprint similarity (not a patent novelty verdict).",
            SimilarityInput,
            {"smiles": "CC(=O)Oc1ccccc1C(=O)O", "limit": 5},
        ),
        "substructure": Operation(
            "Bounded PubChem substructure search for a supplied SMILES core; defaults to "
            "exact stereo, charge and isotope matching. No exhaustive scaffold search is implied.",
            SubstructureInput,
            {"smiles": "CC(=O)Oc1ccccc1C(=O)O", "limit": 5},
        ),
        "assay": Operation(
            "BioAssay description, readout definitions and target metadata by AID.",
            AssayInput,
            {"aid": 1},
        ),
    }
    downloads = {
        "sdf": Operation(
            "Download up to 100 known CIDs as 2D/3D SDF.",
            SDFInput,
            {"cids": [2244], "record_type": "2d"},
        )
    }

    def query(self, operation, params):
        if operation == "batch_properties":
            response = self.request(
                "POST",
                f"{BASE}/compound/inchikey/property/{PROPERTIES}/JSON",
                form={"inchikey": ",".join(params.inchikeys)},
            )
            return Page(
                response.data["PropertyTable"]["Properties"],
                [response.provenance],
                warnings=[
                    "Missing input keys are not present in the returned exact-key records; "
                    "do not map output rows to input order."
                ],
            )
        if operation == "source_categories":
            response = self.request(
                "GET",
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/categories/compound/{params.cid}/JSON",
            )
            data = response.data["SourceCategories"]
            if data["RecordType"] != "CID" or data["RecordNumber"] != params.cid:
                raise ValueError("PubChem source categories returned another record")
            return Page(
                data["Categories"],
                [response.provenance],
                warnings=["Chemical Vendors are depositor links, not verified stock or pricing."],
            )
        if operation == "properties":
            if params.namespace == "smiles":
                response = self.request(
                    "POST",
                    f"{BASE}/compound/smiles/property/{PROPERTIES}/JSON",
                    form={"smiles": params.identifier},
                )
            else:
                identifier = quote(params.identifier, safe="")
                response = self.request(
                    "GET",
                    f"{BASE}/compound/{params.namespace}/{identifier}/property/{PROPERTIES}/JSON",
                )
            return Page(response.data["PropertyTable"]["Properties"], [response.provenance])
        if operation == "assay":
            response = self.request("GET", f"{BASE}/assay/aid/{params.aid}/description/JSON")
            return Page(response.data["PC_AssayContainer"], [response.provenance])
        if operation == "substructure":
            response = self.request(
                "POST",
                f"{BASE}/compound/fastsubstructure/smiles/cids/JSON",
                params={
                    "Stereo": params.stereo,
                    "MatchCharges": str(params.match_charges).lower(),
                    "MatchIsotopes": str(params.match_isotopes).lower(),
                    "RingsNotEmbedded": str(params.rings_not_embedded).lower(),
                    "MaxRecords": params.limit,
                },
                form={"smiles": params.smiles},
            )
            return Page(
                [{"CID": cid} for cid in response.data["IdentifierList"]["CID"]],
                [response.provenance],
                warnings=[
                    "Results are capped by MaxRecords, with unknown total and no continuation; "
                    "they are substructure leads, not verified activity, stock or novelty. "
                    "Unspecified query stereochemistry remains unspecified."
                ],
            )
        response = self.request(
            "POST",
            f"{BASE}/compound/fastsimilarity_2d/smiles/cids/JSON",
            params={"Threshold": params.threshold, "MaxRecords": params.limit},
            form={"smiles": params.smiles},
        )
        rows = [{"CID": cid} for cid in response.data["IdentifierList"]["CID"]]
        warnings = ["Results are capped by MaxRecords; no exhaustive similarity search is implied."]
        return Page(rows, [response.provenance], warnings=warnings)

    def plan(self, operation, params):
        ids = ",".join(map(str, params.cids))
        return self.make_plan(
            operation,
            params,
            url=f"{BASE}/compound/cid/{ids}/SDF?record_type={params.record_type}",
            filename=f"pubchem_{params.cids[0]}_{len(params.cids)}_{params.record_type}.sdf",
            format="sdf",
        )
