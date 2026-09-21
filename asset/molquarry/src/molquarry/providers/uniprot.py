from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import Field

from ..models import InputModel
from .base import Operation, Page, Provider

BASE = "https://rest.uniprot.org/uniprotkb"
ACCESSION = (
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?:-[1-9][0-9]*)?$"
)


class AccessionInput(InputModel):
    accession: str = Field(pattern=ACCESSION)


class SearchInput(InputModel):
    query: str = Field(
        min_length=1,
        max_length=2000,
        description="UniProt query syntax, e.g. gene_exact:EGFR AND organism_id:9606",
    )
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2000)


class UniProt(Provider):
    id = "uniprot"
    download_hosts = frozenset({"rest.uniprot.org"})
    operations = {
        "entry": Operation(
            "Sequence, function, organism and database cross-references.",
            AccessionInput,
            {"accession": "P00533"},
        ),
        "search": Operation(
            "UniProt query language search with cursor pagination.",
            SearchInput,
            {"query": "gene_exact:EGFR AND organism_id:9606 AND reviewed:true"},
        ),
    }
    downloads = {
        "fasta": Operation(
            "Download the sequence of a UniProt accession as FASTA.",
            AccessionInput,
            {"accession": "P00533"},
        )
    }

    def query(self, operation, params):
        if operation == "entry":
            response = self.request("GET", f"{BASE}/{params.accession}.json")
            return Page([response.data], [response.provenance])
        response = self.request(
            "GET",
            f"{BASE}/search",
            params={
                "query": params.query,
                "size": params.limit,
                "format": "json",
                "cursor": params.cursor,
            },
        )
        links = httpx.Response(200, headers=response.headers).links
        next_params = None
        if "next" in links:
            # Extract the opaque cursor; never fetch an arbitrary Link header URL.
            cursor = parse_qs(urlparse(links["next"]["url"]).query)["cursor"][0]
            next_params = {**params.model_dump(), "cursor": cursor}
        total = response.headers.get("x-total-results")
        return Page(
            response.data["results"],
            [response.provenance],
            int(total) if total is not None else None,
            next_params,
        )

    def plan(self, operation, params):
        return self.make_plan(
            operation,
            params,
            url=f"{BASE}/{params.accession}.fasta",
            filename=f"{params.accession}.fasta",
            format="fasta",
        )
