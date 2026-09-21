"""Chemical identity, pharmacology, and patent-chemistry APIs."""

from typing import Literal
from urllib.parse import quote

from pydantic import Field, model_validator

from ..errors import MolQuarryError
from ..models import InputModel
from .base import NoParams, Operation, Page, PageInput, Provider
from .common import Accession, Search, Text, credential, local_page, offset_page, records


class BindingSearch(PageInput):
    uniprot: Accession
    cutoff_nm: float = Field(default=10000, gt=0, le=10000000)


class BindingDB(Provider):
    id = "bindingdb"
    operations = {
        "by_uniprot": Operation(
            (
                "BindingDB measurements below a source-side nM cutoff; keep original endpoint "
                "types and relations"
            ),
            BindingSearch,
            {"uniprot": "P00533", "cutoff_nm": 1.0, "limit": 5},
        )
    }

    def query(self, operation, params):
        r = self.request(
            "GET",
            "https://bindingdb.org/rest/getLigandsByUniprots",
            params={
                "uniprot": params.uniprot,
                "cutoff": params.cutoff_nm,
                "response": "application/json",
            },
            empty_ok=True,
        )
        rows = [] if r.data is None else r.data["getLindsByUniprotsResponse"]["affinities"]
        if isinstance(rows, dict):
            rows = [rows]
        return local_page(
            rows,
            r,
            params,
            warnings=[
                (
                    "BindingDB REST is unpaginated; this page slices a bounded full response. "
                    "Use bulk for training."
                ),
                (
                    "affinity_type and affinity retain upstream semantics; Ki, Kd and IC50 are "
                    "not interchangeable."
                ),
            ],
        )


class ChEBIEntry(InputModel):
    chebi_id: str = Field(pattern=r"^(?:CHEBI:)?\d+$")


class ChEBISearch(InputModel):
    query: Text
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


class ChEBI(Provider):
    id = "chebi"
    operations = {
        "compound": Operation(
            "ChEBI 2 compound identity and ontology annotations",
            ChEBIEntry,
            {"chebi_id": "CHEBI:15365"},
        ),
        "search": Operation(
            "Text/name/structure identifier search", ChEBISearch, {"query": "aspirin", "limit": 5}
        ),
        "parents": Operation(
            "Direct ontology parent relationships", ChEBIEntry, {"chebi_id": "CHEBI:15365"}
        ),
    }
    downloads = {
        "molfile": Operation(
            "Original ChEBI structure MOL file", ChEBIEntry, {"chebi_id": "CHEBI:15365"}
        )
    }
    download_hosts = frozenset({"www.ebi.ac.uk"})
    base = "https://www.ebi.ac.uk/chebi/backend/api/public"

    def query(self, operation, params):
        if operation == "search":
            r = self.request(
                "GET",
                f"{self.base}/es_search/",
                params={"term": params.query, "page": params.page, "size": params.limit},
            )
            return Page(
                r.data["results"],
                [r.provenance],
                r.data["total"],
                {**params.model_dump(), "page": params.page + 1}
                if params.page < r.data["number_pages"]
                else None,
            )
        key = params.chebi_id.removeprefix("CHEBI:")
        route = f"compound/{key}" if operation == "compound" else f"ontology/parents/{key}"
        r = self.request("GET", f"{self.base}/{route}/")
        return Page(records(r.data), [r.provenance])

    def plan(self, operation, params):
        key = params.chebi_id.removeprefix("CHEBI:")
        return self.make_plan(
            operation,
            params,
            url=f"{self.base}/molfile/{key}/",
            filename=f"CHEBI_{key}.mol",
            format="mol",
        )


class UniChemMapping(InputModel):
    compound: Text
    type: Literal["sourceID", "inchikey", "inchi", "uci"] = "sourceID"
    source_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def source_required(self):
        if self.type == "sourceID" and self.source_id is None:
            raise ValueError("source_id is required for sourceID mapping")
        return self


class UniChem(Provider):
    id = "unichem"
    operations = {
        "sources": Operation(
            "Current UniChem source identifiers and update metadata", NoParams, {}
        ),
        "mapping": Operation(
            "Structure-based cross-database mapping; specify source ID namespace",
            UniChemMapping,
            {"compound": "CHEMBL25", "source_id": 1},
        ),
    }

    def query(self, operation, params):
        if operation == "sources":
            r = self.request("GET", "https://www.ebi.ac.uk/unichem/api/v1/sources/")
            return Page(r.data["sources"], [r.provenance])
        body = {"compound": params.compound, "type": params.type}
        if params.source_id is not None:
            body["sourceID"] = params.source_id
        r = self.request("POST", "https://www.ebi.ac.uk/unichem/api/v1/compounds", body=body)
        return Page(r.data["compounds"], [r.provenance])


class GtoPLigands(Search):
    pass


class GtoPEntry(InputModel):
    identifier: int = Field(ge=1)


class GtoPInteractions(GtoPEntry, PageInput):
    species: Text = "Human"


class GtoPdb(Provider):
    id = "gtopdb"
    operations = {
        "ligands": Operation(
            "Find curated ligands by name", GtoPLigands, {"query": "aspirin"}, ("GTOPDB_API_KEY",)
        ),
        "targets": Operation(
            "Find curated targets by name", GtoPLigands, {"query": "EGFR"}, ("GTOPDB_API_KEY",)
        ),
        "ligand": Operation(
            "Curated ligand metadata", GtoPEntry, {"identifier": 4139}, ("GTOPDB_API_KEY",)
        ),
        "interactions": Operation(
            "Target interactions with source affinity and species",
            GtoPInteractions,
            {"identifier": 1797},
            ("GTOPDB_API_KEY",),
        ),
    }

    def query(self, operation, params):
        headers = {"GTP-API-Key": credential("GTOPDB_API_KEY", self.id)}
        route = {
            "ligand": f"ligands/{getattr(params, 'identifier', '')}",
            "interactions": f"targets/{getattr(params, 'identifier', '')}/interactions",
        }.get(operation, operation)
        query = (
            {"name": params.query}
            if operation in {"ligands", "targets"}
            else {"species": params.species}
            if operation == "interactions"
            else {}
        )
        r = self.request(
            "GET",
            f"https://www.guidetopharmacology.org/services/{route}",
            params=query,
            headers=headers,
            private=True,
        )
        return (
            Page(records(r.data), [r.provenance])
            if operation == "ligand"
            else local_page(r.data, r, params)
        )


class SureChemical(InputModel):
    identifier: str = Field(pattern=r"^(?:SCHEMBL)?\d+$")


class SureName(InputModel):
    name: Text


class SureSmiles(InputModel):
    smiles: Text


class PatentFamily(InputModel):
    publication: str = Field(pattern=r"^[A-Za-z]{2}[A-Za-z0-9-]{3,30}$")


class SureChEMBL(Provider):
    id = "surechembl"
    operations = {
        "chemical": Operation(
            "SureChEMBL compound identity", SureChemical, {"identifier": "SCHEMBL1353"}
        ),
        "chemical_by_name": Operation(
            "Resolve a chemical name in patent chemistry", SureName, {"name": "aspirin"}
        ),
        "chemical_by_smiles": Operation(
            "Resolve a structure in patent chemistry",
            SureSmiles,
            {"smiles": "CC(=O)Oc1ccccc1C(=O)O"},
        ),
        "family": Operation(
            "Patent family members for a publication",
            PatentFamily,
            {"publication": "US20160355508A1"},
        ),
    }

    def query(self, operation, params):
        provenance = []
        if operation == "chemical":
            route = f"chemical/id/{params.identifier.removeprefix('SCHEMBL')}"
        elif operation == "chemical_by_name":
            route = "chemical/name/" + quote(params.name, safe="")
        elif operation == "chemical_by_smiles":
            route = "chemical/smiles/" + quote(params.smiles, safe="") + "/"
        else:
            normalized = self.request(
                "GET",
                "https://www.surechembl.org/api/document/identifier/normalized/"
                + params.publication,
            )
            matches = normalized.data["data"][params.publication]
            if len(matches) != 1:
                raise MolQuarryError(
                    "ambiguous_identifier",
                    "Supply a publication including kind code",
                    source=self.id,
                )
            route = f"document/{quote(matches[0], safe='')}/family/members"
            provenance.append(normalized.provenance)
        r = self.request("GET", "https://www.surechembl.org/api/" + route)
        if r.data["status"] != "OK":
            raise MolQuarryError(
                "upstream_error", "SureChEMBL reported an unsuccessful operation", source=self.id
            )
        payload = r.data["data"]
        if operation == "chemical_by_smiles":
            # This endpoint keys its envelope by the submitted SMILES. The query is
            # already in parameters; expose the chemical's fields like the ID/name lookups.
            payload = payload[params.smiles] if payload else None
        return Page(
            records(payload),
            provenance + [r.provenance],
            warnings=[
                (
                    "Patent occurrence/family metadata is evidence for review, not a novelty or "
                    "FTO verdict."
                )
            ],
        )


class LotusStructure(PageInput):
    smiles: Text


class LOTUS(Provider):
    id = "lotus"
    operations = {
        "search": Operation(
            "Natural product name/LOTUS ID/InChIKey search", Search, {"query": "LTS0253154"}
        ),
        "exact": Operation(
            "Source exact-structure search; results can include different stereochemistry",
            LotusStructure,
            {"smiles": "O=C1OC(C(O)=C1O)CO"},
        ),
    }

    def query(self, operation, params):
        route = "simple" if operation == "search" else "exact-structure"
        query = (
            {"query": params.query}
            if operation == "search"
            else {"smiles": params.smiles, "type": "inchi"}
        )
        r = self.request(
            "GET", f"https://lotus.naturalproducts.net/api/search/{route}", params=query
        )
        data = r.data
        if isinstance(data, dict):
            data = data["naturalProducts"]
        return local_page(
            data,
            r,
            params,
            warnings=[
                "LOTUS exact-structure results may include different stereochemistry; "
                "compare each returned full InChIKey before treating it as an exact identity."
            ]
            if operation == "exact"
            else [],
        )


class COCONUT(Provider):
    id = "coconut"
    operations = {
        "search": Operation(
            "Natural product name, SMILES, InChI or InChIKey search (public advanced-search API)",
            Search,
            {"query": "caffeine", "limit": 5},
        )
    }

    def query(self, operation, params):
        if params.offset % params.limit:
            raise MolQuarryError("invalid_parameters", "offset must be a multiple of limit")
        r = self.request(
            "POST",
            "https://coconut.naturalproducts.net/api/search",
            body={
                "query": params.query,
                "limit": params.limit,
                "offset": params.offset,
                "page": params.offset // params.limit + 1,
            },
        )
        envelope = r.data["data"]
        return offset_page(envelope["data"], r, params, envelope["total"])
