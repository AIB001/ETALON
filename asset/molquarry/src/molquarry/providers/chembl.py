from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..models import InputModel
from .base import Operation, Page, PageInput, Provider

BASE = "https://www.ebi.ac.uk/chembl/api/data"


class ChemblID(InputModel):
    chembl_id: str = Field(pattern=r"^CHEMBL[0-9]+$")


class TextSearch(PageInput):
    query: str = Field(min_length=1, max_length=500)


class TargetSearch(PageInput):
    uniprot: str = Field(pattern=r"^[A-Z0-9]{6,10}$")


class MoleculeKeys(PageInput):
    inchikeys: list[Annotated[str, Field(pattern=r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")]] = Field(
        min_length=1, max_length=50
    )


class BatchDetails(PageInput):
    entity: Literal["molecule", "assay", "document"]
    chembl_ids: list[Annotated[str, Field(pattern=r"^CHEMBL[0-9]+$")]] = Field(
        min_length=1, max_length=50
    )


class ActivityInput(PageInput):
    assay_chembl_id: str | None = Field(default=None, pattern=r"^CHEMBL[0-9]+$")
    document_chembl_id: str | None = Field(default=None, pattern=r"^CHEMBL[0-9]+$")
    target_chembl_id: str | None = Field(default=None, pattern=r"^CHEMBL[0-9]+$")
    molecule_chembl_id: str | None = Field(default=None, pattern=r"^CHEMBL[0-9]+$")
    standard_type: Literal["Ki", "Kd", "IC50", "EC50", "AC50", "GI50", "Potency"] | None = None
    assay_type: Literal["B", "F", "A", "T", "P", "U"] | None = None
    standard_relation: Literal["=", "<", ">", "<=", ">="] | None = None

    @model_validator(mode="after")
    def require_filter(self):
        if not (
            self.target_chembl_id
            or self.molecule_chembl_id
            or self.document_chembl_id
            or self.assay_chembl_id
        ):
            raise ValueError("Provide a target, molecule, document or assay ChEMBL ID")
        return self


class ChEMBL(Provider):
    id = "chembl"
    download_hosts = frozenset({"www.ebi.ac.uk"})
    operations = {
        "molecules_by_inchikey": Operation(
            "Exact full InChIKey lookup for up to 50 structures; absence applies to this source.",
            MoleculeKeys,
            {"inchikeys": ["BSYNRYMUTXBXSQ-UHFFFAOYSA-N"], "limit": 100},
        ),
        "batch_details": Operation(
            "Retrieve up to 50 explicit molecule, assay or document IDs with pagination.",
            BatchDetails,
            {"entity": "molecule", "chembl_ids": ["CHEMBL25"], "limit": 100},
        ),
        "molecule": Operation(
            "Compound details, hierarchy and source structure.", ChemblID, {"chembl_id": "CHEMBL25"}
        ),
        "target": Operation(
            "Target type, organism and component accessions.", ChemblID, {"chembl_id": "CHEMBL203"}
        ),
        "assay": Operation(
            "Assay description, organism, target confidence, variant and experimental context.",
            ChemblID,
            {"chembl_id": "CHEMBL5651835"},
        ),
        "document": Operation(
            "Source document DOI/PubMed identity for primary-evidence review.",
            ChemblID,
            {"chembl_id": "CHEMBL5649169"},
        ),
        "search_molecules": Operation(
            "Full-text molecule search with explicit pagination.",
            TextSearch,
            {"query": "aspirin", "limit": 5},
        ),
        "targets_by_uniprot": Operation(
            "Map a UniProt accession to ChEMBL targets/components.",
            TargetSearch,
            {"uniprot": "P00533"},
        ),
        "activities": Operation(
            "Filtered experimental activities; retains assay, units, relation and document fields.",
            ActivityInput,
            {"target_chembl_id": "CHEMBL203", "standard_type": "IC50", "limit": 5},
        ),
        "search_assays": Operation(
            "Find assays mentioning a target/alias in the description, including Unchecked "
            "and complex assays missed by UniProt target mapping. Mentions require review.",
            TextSearch,
            {"query": "MTDH", "limit": 5},
        ),
    }
    downloads = {
        "sdf": Operation(
            "Download the source molecule structure as SDF.", ChemblID, {"chembl_id": "CHEMBL25"}
        )
    }

    def query(self, operation, params):
        if operation in {"molecule", "target", "assay", "document"}:
            response = self.request("GET", f"{BASE}/{operation}/{params.chembl_id}.json")
            return Page([response.data], [response.provenance])
        query = params.model_dump(exclude_none=True)
        if operation == "batch_details":
            path = query.pop("entity")
            key = {"molecule": "molecules", "assay": "assays", "document": "documents"}[path]
            query[f"{path}_chembl_id__in"] = ",".join(query.pop("chembl_ids"))
        elif operation == "molecules_by_inchikey":
            path, key = "molecule", "molecules"
            query["molecule_structures__standard_inchi_key__in"] = ",".join(query.pop("inchikeys"))
        elif operation == "search_molecules":
            path, key = "molecule/search", "molecules"
            query["q"] = query.pop("query")
        elif operation == "targets_by_uniprot":
            path, key = "target", "targets"
            query["target_components__accession"] = query.pop("uniprot")
        elif operation == "search_assays":
            path, key = "assay", "assays"
            query["description__icontains"] = query.pop("query")
        else:
            path, key = "activity", "activities"
        response = self.request("GET", f"{BASE}/{path}.json", params=query)
        meta = response.data["page_meta"]
        next_params = None
        if meta.get("next"):
            next_params = params.model_dump(exclude_none=True)
            next_params["offset"] = meta["offset"] + meta["limit"]
        warnings = []
        if operation == "activities":
            warnings.append(
                "Ki/Kd/IC50/EC50 and censored relations retain their source meaning; "
                "MolQuarry does not merge them into an affinity label."
            )
        return Page(
            response.data[key], [response.provenance], meta["total_count"], next_params, warnings
        )

    def plan(self, operation, params):
        return self.make_plan(
            operation,
            params,
            url=f"{BASE}/molecule/{params.chembl_id}.sdf",
            filename=f"{params.chembl_id}.sdf",
            format="sdf",
            notes=["The source's single-record SDF may omit trailing $$$$; bytes are preserved."],
        )
