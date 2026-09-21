"""Public, source-specific structure and target-biology services."""

from typing import Literal
from urllib.parse import quote

from pydantic import Field

from ..models import InputModel
from .base import NoParams, Operation, Page, PageInput, Provider
from .common import Accession, Identifier, Search, Text, local_page, records


class Protein(InputModel):
    entry_name: str = Field(pattern=r"^[a-z0-9]+_[a-z0-9]+$")


class GPCRdb(Provider):
    id = "gpcrdb"
    operations = {
        "protein": Operation(
            "Sequence, family and generic numbering", Protein, {"entry_name": "adrb2_human"}
        ),
        "structures": Operation(
            "Experimental structures for one receptor", Protein, {"entry_name": "adrb2_human"}
        ),
        "residues": Operation(
            "Residues and generic numbering", Protein, {"entry_name": "adrb2_human"}
        ),
    }

    def query(self, operation, params):
        route = {
            "protein": "protein",
            "structures": "structure/protein",
            "residues": "residues/extended",
        }[operation]
        r = self.request("GET", f"https://gpcrdb.org/services/{route}/{params.entry_name}/")
        return Page(records(r.data), [r.provenance])


class KinaseSearch(PageInput):
    name: str = Field(default="", max_length=100)
    species: Literal["Human", "Mouse"] | None = None


class KinaseStructures(PageInput):
    kinase_id: int = Field(ge=1)


class KLIFS(Provider):
    id = "klifs"
    operations = {
        "kinases": Operation(
            "List kinases; filter the returned official kinase registry locally",
            KinaseSearch,
            {"name": "EGFR"},
        ),
        "structures": Operation(
            "Structures for a KLIFS kinase ID", KinaseStructures, {"kinase_id": 406, "limit": 5}
        ),
    }

    def query(self, operation, params):
        if operation == "kinases":
            r = self.request("GET", "https://klifs.net/api/kinase_names")
            rows = [
                x
                for x in r.data
                if params.name.casefold() in x["name"].casefold()
                and (not params.species or x["species"] == params.species)
            ]
        else:
            r = self.request(
                "GET",
                "https://klifs.net/api/structures_list",
                params={"kinase_ID": params.kinase_id},
            )
            rows = r.data
        return local_page(rows, r, params)


class Gene(InputModel):
    ensembl_id: str = Field(pattern=r"^ENSG\d{11}$")


class HPASearch(Search):
    columns: str = Field(default="g,eg,gs,gd,up", pattern=r"^[A-Za-z0-9_,]+$", max_length=500)


class HPA(Provider):
    id = "hpa"
    operations = {
        "gene": Operation(
            "Gene annotations and expression evidence", Gene, {"ensembl_id": "ENSG00000146648"}
        ),
        "search": Operation(
            "HPA documented search_download API; local result pagination",
            HPASearch,
            {"query": "EGFR", "limit": 5},
        ),
    }

    def query(self, operation, params):
        if operation == "gene":
            r = self.request("GET", f"https://www.proteinatlas.org/{params.ensembl_id}.json")
            return Page(records(r.data), [r.provenance])
        r = self.request(
            "GET",
            "https://www.proteinatlas.org/api/search_download.php",
            params={
                "search": params.query,
                "format": "json",
                "columns": params.columns,
                "compress": "no",
            },
        )
        return local_page(r.data, r, params)


class GTExPage(InputModel):
    page: int = Field(default=0, ge=0, le=1000000)
    limit: int = Field(default=20, ge=1, le=100)


class GTExGene(GTExPage):
    query: Text
    gencode_version: Literal["v39", "v26", "v19"] = "v39"


class GTExExpression(GTExPage):
    gencode_id: str = Field(pattern=r"^ENSG\d{11}\.\d+$")
    dataset: str = Field(default="gtex_v10", pattern=r"^[A-Za-z0-9_]+$")
    tissue: Accession | None = None


class GTEx(Provider):
    id = "gtex"
    operations = {
        "genes": Operation("Resolve symbol to versioned GENCODE IDs", GTExGene, {"query": "EGFR"}),
        "expression": Operation(
            "Median tissue expression for one versioned GENCODE ID",
            GTExExpression,
            {"gencode_id": "ENSG00000146648.20"},
        ),
        "eqtl": Operation(
            "Significant single-tissue eQTLs; preserve dataset/tissue context",
            GTExExpression,
            {"gencode_id": "ENSG00000146648.20", "limit": 5},
        ),
        "datasets": Operation("Dataset metadata", NoParams, {}),
    }

    def query(self, operation, params):
        if operation == "datasets":
            r = self.request("GET", "https://gtexportal.org/api/v2/metadata/dataset")
            return Page(
                records(r.data.get("data", r.data) if isinstance(r.data, dict) else r.data),
                [r.provenance],
            )
        query = {"page": params.page, "itemsPerPage": params.limit}
        if operation == "genes":
            route = "reference/geneSearch"
            query.update(
                geneId=params.query,
                gencodeVersion=params.gencode_version,
                genomeBuild="GRCh38/hg38",
            )
        else:
            route = (
                "expression/medianGeneExpression"
                if operation == "expression"
                else "association/singleTissueEqtl"
            )
            query.update(
                gencodeId=params.gencode_id,
                datasetId=params.dataset,
                tissueSiteDetailId=params.tissue,
            )
        r = self.request("GET", f"https://gtexportal.org/api/v2/{route}", params=query)
        rows, info = r.data["data"], r.data["paging_info"]
        total = info.get("totalNumberOfItems")
        more = info.get(
            "hasNextPage",
            (params.page + 1) * params.limit < total
            if total is not None
            else len(rows) == params.limit,
        )
        return Page(
            rows,
            [r.provenance],
            total,
            {**params.model_dump(), "page": params.page + 1} if more and rows else None,
        )


class StringQuery(PageInput):
    identifiers: list[Accession] = Field(min_length=1, max_length=50)
    species: int = Field(default=9606, ge=1)
    required_score: int = Field(default=400, ge=0, le=1000)


class STRING(Provider):
    id = "string"
    operations = {
        "map_ids": Operation(
            "Map names to STRING protein IDs; review ambiguous mappings",
            StringQuery,
            {"identifiers": ["EGFR"]},
        ),
        "network": Operation(
            "Functional associations among supplied proteins",
            StringQuery,
            {"identifiers": ["EGFR", "GRB2", "KRAS"]},
        ),
        "partners": Operation(
            "Interaction partners, with evidence scores",
            StringQuery,
            {"identifiers": ["EGFR"], "limit": 5},
        ),
        "enrichment": Operation(
            "Functional enrichment of a supplied gene set",
            StringQuery,
            {"identifiers": ["EGFR", "GRB2", "KRAS"]},
        ),
        "version": Operation("Current STRING version and stable API host", NoParams, {}),
    }

    def query(self, operation, params):
        if operation == "version":
            r = self.request("GET", "https://string-db.org/api/json/version")
            return Page(records(r.data), [r.provenance])
        route = {
            "map_ids": "get_string_ids",
            "network": "network",
            "partners": "interaction_partners",
            "enrichment": "enrichment",
        }[operation]
        body = {
            "identifiers": "\r".join(params.identifiers),
            "species": params.species,
            "caller_identity": "MolQuarry",
        }
        if operation in {"network", "partners"}:
            body["required_score"] = params.required_score
        if operation == "network":
            body["add_nodes"] = 0
        if operation == "partners":
            # Limit determines the upstream neighbor set; local pages paginate that fixed set.
            body["limit"] = 100
        if operation == "map_ids":
            body.update(limit=5, echo_query=1)
        r = self.request("POST", f"https://string-db.org/api/json/{route}", form=body)
        return local_page(
            r.data,
            r,
            params,
            warnings=[
                (
                    "STRING associations include functional evidence; they are not all direct "
                    "physical binding."
                ),
                (
                    "Pagination is local; partners is capped at 100 neighbors, map_ids at 5 "
                    "matches per input."
                ),
            ],
        )


class Reactome(Provider):
    id = "reactome"
    operations = {
        "pathway": Operation(
            "Reactome stable-ID object and evidence", Identifier, {"identifier": "R-HSA-177929"}
        ),
        "pathways_by_uniprot": Operation(
            "Pathway mapping for a UniProt accession", Identifier, {"identifier": "P00533"}
        ),
    }

    def query(self, operation, params):
        key = quote(params.identifier, safe="")
        route = (
            f"data/query/{key}"
            if operation == "pathway"
            else f"data/mapping/UniProt/{key}/pathways"
        )
        r = self.request("GET", f"https://reactome.org/ContentService/{route}")
        return Page(records(r.data), [r.provenance])


class PGxChemical(InputModel):
    name: Text
    view: Literal["min", "base", "max"] = "base"


class PGxGene(InputModel):
    symbol: Accession
    view: Literal["min", "base", "max"] = "base"


class PGxEntry(InputModel):
    identifier: str = Field(pattern=r"^PA\d+$")
    entity: Literal["chemical", "gene", "variant", "pathway", "guidelineAnnotation", "label"] = (
        "chemical"
    )


class ClinPGx(Provider):
    id = "clinpgx"
    operations = {
        "chemical": Operation(
            "Find drug/chemical pharmacogenomics annotations", PGxChemical, {"name": "warfarin"}
        ),
        "gene": Operation("Find gene pharmacogenomics annotations", PGxGene, {"symbol": "CYP2C9"}),
        "entry": Operation(
            "Retrieve a ClinPGx PA identifier", PGxEntry, {"identifier": "PA451906"}
        ),
    }

    def query(self, operation, params):
        route = f"{params.entity}/{params.identifier}" if operation == "entry" else operation
        r = self.request(
            "GET",
            f"https://api.clinpgx.org/v1/data/{route}",
            params={"view": "base"} if operation == "entry" else params.model_dump(),
        )
        data = r.data["data"] if isinstance(r.data, dict) and "data" in r.data else r.data
        return Page(records(data), [r.provenance])
