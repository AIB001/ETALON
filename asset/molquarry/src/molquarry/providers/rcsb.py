from pydantic import Field

from ..models import InputModel
from .base import Operation, Page, PageInput, Provider

DATA = "https://data.rcsb.org/rest/v1/core"
SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"


class EntryInput(InputModel):
    pdb_id: str = Field(
        pattern=r"^[0-9][a-zA-Z0-9]{3}$", description="Legacy four-character PDB ID"
    )


class LigandInput(InputModel):
    ccd_id: str = Field(pattern=r"^[A-Z0-9]{1,5}$")


class EntityInput(EntryInput):
    entity_id: str = Field(pattern=r"^[1-9][0-9]{0,5}$")


class TextSearch(PageInput):
    query: str = Field(min_length=1, max_length=500)


class UniProtSearch(PageInput):
    uniprot: str = Field(pattern=r"^[A-Z0-9]{6,10}$")


class RCSB(Provider):
    id = "rcsb"
    download_hosts = frozenset({"files.rcsb.org"})
    operations = {
        "entry": Operation(
            "Experimental structure metadata by PDB ID.", EntryInput, {"pdb_id": "7KNW"}
        ),
        "ligand": Operation(
            "Chemical Component Dictionary metadata by CCD ID.", LigandInput, {"ccd_id": "ATP"}
        ),
        "nonpolymer_entity": Operation(
            "Map an entry's non-polymer entity to CCD ligand IDs and source annotations.",
            EntityInput,
            {"pdb_id": "7KNX", "entity_id": "2"},
        ),
        "polymer_entity": Operation(
            "Verify protein sequence, species, construct and UniProt mapping in a complex.",
            EntityInput,
            {"pdb_id": "7KNX", "entity_id": "1"},
        ),
        "search": Operation(
            "Search experimental PDB entries by text.", TextSearch, {"query": "EGFR", "limit": 5}
        ),
        "by_uniprot": Operation(
            "Find experimental PDB entries with a matching UniProt reference.",
            UniProtSearch,
            {"uniprot": "P00533", "limit": 5},
        ),
    }
    downloads = {
        "structure": Operation(
            "Download original mmCIF coordinates (no preparation).", EntryInput, {"pdb_id": "7KNW"}
        )
    }

    def query(self, operation, params):
        if operation in {"nonpolymer_entity", "polymer_entity"}:
            response = self.request(
                "GET", f"{DATA}/{operation}/{params.pdb_id.upper()}/{params.entity_id}"
            )
            return Page([response.data], [response.provenance])
        if operation in {"entry", "ligand"}:
            path = (
                f"entry/{params.pdb_id.upper()}"
                if operation == "entry"
                else f"chemcomp/{params.ccd_id}"
            )
            response = self.request("GET", f"{DATA}/{path}")
            return Page([response.data], [response.provenance])
        if operation == "search":
            query = {
                "type": "terminal",
                "service": "full_text",
                "parameters": {"value": params.query},
            }
        else:
            prefix = "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers."
            query = {
                "type": "group",
                "logical_operator": "and",
                "nodes": [
                    {
                        "type": "terminal",
                        "service": "text",
                        "parameters": {
                            "attribute": prefix + "database_accession",
                            "operator": "exact_match",
                            "value": params.uniprot,
                        },
                    },
                    {
                        "type": "terminal",
                        "service": "text",
                        "parameters": {
                            "attribute": prefix + "database_name",
                            "operator": "exact_match",
                            "value": "UniProt",
                        },
                    },
                ],
            }
        body = {
            "query": query,
            "return_type": "entry",
            "request_options": {
                "paginate": {"start": params.offset, "rows": params.limit},
                "results_content_type": ["experimental"],
            },
        }
        response = self.request("POST", SEARCH, body=body, empty_ok=True)
        if response.data is None:
            return Page([], [response.provenance], 0)
        total = response.data["total_count"]
        rows = response.data.get("result_set", [])
        next_params = None
        if rows and params.offset + len(rows) < total:
            next_params = params.model_dump()
            next_params["offset"] += len(rows)
        return Page(rows, [response.provenance], total, next_params)

    def plan(self, operation, params):
        identifier = params.pdb_id.upper()
        return self.make_plan(
            operation,
            params,
            url=f"https://files.rcsb.org/download/{identifier}.cif",
            filename=f"{identifier}.cif",
            format="mmcif",
            notes=["Coordinates retain source chemistry and experimental context."],
        )
