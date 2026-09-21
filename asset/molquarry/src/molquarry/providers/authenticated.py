"""Credentialed read/search operations. Keys remain in environment and HTTP headers."""

from typing import Literal
from urllib.parse import quote

from pydantic import Field

from .._version import __version__
from ..models import InputModel
from .base import Operation, Page, PageInput, Provider
from .common import Accession, Search, Text, credential, local_page, offset_page, records


class RealSearch(InputModel):
    smiles_list: list[Text] = Field(min_length=1, max_length=20)
    stereo: Literal["exact-stereo", "any-stereo"] = "exact-stereo"


class Enamine(Provider):
    id = "enamine"
    operations = {
        "exact": Operation(
            "Search REAL Space for a small batch of structures",
            RealSearch,
            {"smiles_list": ["CCOc1ccc(NC(=O)C)cc1"]},
            ("REAL_API_KEY",),
        )
    }

    def query(self, operation, params):
        r = self.request(
            "POST",
            f"https://real.enamine.net/api/v1/space/real/search-structure/batch/{params.stereo}",
            body={"smiles_list": params.smiles_list},
            headers={
                "X-API-KEY": credential("REAL_API_KEY", self.id),
                "User-Agent": f"Mozilla/5.0 (compatible; MolQuarry/{__version__})",
            },
            private=True,
        )
        return Page(
            records(r.data),
            [r.provenance],
            warnings=[
                (
                    "Make-on-demand search results are not a confirmed synthesis quote or stock "
                    "reservation."
                )
            ],
        )


class ChemspaceSearch(InputModel):
    smiles: Text
    mode: Literal["exact", "sub", "sim"] = "exact"
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    categories: list[Literal["CSSB", "CSSS", "CSMB", "CSMS", "CSCS"]] = Field(
        default_factory=lambda: ["CSSB", "CSSS"], min_length=1, max_length=5
    )
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


class Chemspace(Provider):
    id = "chemspace"
    operations = {
        "search": Operation(
            "Chemspace v5 structure search with supplier offers, region and prices",
            ChemspaceSearch,
            {"smiles": "CC(N)=O", "limit": 5},
            ("CHEMSPACE_API_KEY",),
        )
    }

    def query(self, operation, params):
        r = self.request(
            "POST",
            f"https://api.chem-space.com/v5/search/{params.mode}",
            body={
                "search": {
                    "smiles": params.smiles,
                    "shipToCountry": params.country,
                    "categories": params.categories,
                },
                "page": params.page,
                "pageSize": params.limit,
            },
            headers={"X-API-Key": credential("CHEMSPACE_API_KEY", self.id)},
            private=True,
        )
        rows, total = r.data["items"], r.data["filtered"]
        return Page(
            rows,
            [r.provenance],
            total,
            {**params.model_dump(), "page": params.page + 1}
            if params.page * params.limit < total and rows
            else None,
            [
                (
                    "Offers reflect this retrieval time and shipping country; confirm price and "
                    "stock before purchase."
                )
            ],
        )


class MolportSearch(InputModel):
    identifiers: list[Text] = Field(min_length=1, max_length=100)
    identifier_type: Literal["smiles", "molport id"] = "smiles"
    match_types: list[Literal["perfect", "exact", "racemate", "any"]] = Field(
        default_factory=lambda: ["perfect"], min_length=1, max_length=4
    )


class SearchKey(InputModel):
    search_key: Accession


class MolPort(Provider):
    id = "molport"
    operations = {
        "availability": Operation(
            "Submit an asynchronous compound availability search; returns search_key",
            MolportSearch,
            {"identifiers": ["CC(=O)O"]},
            ("MOLPORT_API_KEY",),
        ),
        "results": Operation(
            "Read status and results of an availability search",
            SearchKey,
            {"search_key": "5MNBPTF906Q9M1J5OF2Q9B"},
            ("MOLPORT_API_KEY",),
        ),
    }

    def query(self, operation, params):
        headers = {"X-API-Key": credential("MOLPORT_API_KEY", self.id)}
        base = "https://api.molport.com/v1/availability-searches"
        if operation == "availability":
            r = self.request(
                "POST",
                base,
                body={
                    "search_items_type": params.identifier_type,
                    "search_items": params.identifiers,
                    "match_types": params.match_types,
                    "search_name": "MolQuarry availability",
                },
                headers=headers,
                private=True,
            )
        else:
            r = self.request("GET", f"{base}/{params.search_key}", headers=headers, private=True)
        return Page(
            records(r.data),
            [r.provenance],
            warnings=[
                (
                    "Asynchronous search/status envelope; poll results using search_key. No "
                    "purchase order is submitted."
                )
            ],
        )


class Lens(Provider):
    id = "lens"
    operations = {
        "search": Operation(
            "Search Lens patent text; preserve patent metadata and family context",
            Search,
            {"query": "EGFR inhibitor", "limit": 5},
            ("LENS_API_TOKEN",),
        )
    }

    def query(self, operation, params):
        r = self.request(
            "POST",
            "https://api.lens.org/patent/search",
            body={
                "query": {"query_string": {"query": params.query}},
                "size": params.limit,
                "from": params.offset,
            },
            headers={"Authorization": "Bearer " + credential("LENS_API_TOKEN", self.id)},
            private=True,
        )
        return offset_page(r.data["data"], r, params, r.data["total"])


class Toxicity(PageInput):
    dtxsid: str = Field(pattern=r"^DTXSID\d+$")


class CompTox(Provider):
    id = "comptox"
    operations = {
        "chemical": Operation(
            "CTX exact chemical identifier lookup",
            Search,
            {"query": "toluene"},
            ("COMPTOX_API_KEY",),
        ),
        "toxval": Operation(
            "ToxValDB hazard records for one DSSTox substance",
            Toxicity,
            {"dtxsid": "DTXSID7021360", "limit": 5},
            ("COMPTOX_API_KEY",),
        ),
    }

    def query(self, operation, params):
        route = (
            "chemical/search/equal/" + quote(params.query, safe="")
            if operation == "chemical"
            else f"hazard/toxval/search/by-dtxsid/{params.dtxsid}"
        )
        r = self.request(
            "GET",
            "https://comptox.epa.gov/ctx-api/" + route,
            headers={"x-api-key": credential("COMPTOX_API_KEY", self.id)},
            private=True,
        )
        return local_page(records(r.data), r, params)
