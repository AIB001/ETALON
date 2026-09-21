from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field

from ..errors import MolQuarryError
from .base import Operation, Page, Provider
from .uniprot import AccessionInput


class StructureInput(AccessionInput):
    format: Literal["cif", "pdb", "pae"] = "cif"
    model_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")


class AlphaFold(Provider):
    id = "alphafold"
    download_hosts = frozenset({"alphafold.ebi.ac.uk"})
    operations = {
        "prediction": Operation(
            "Predicted models, confidence and versioned download URLs by UniProt accession.",
            AccessionInput,
            {"accession": "P00533"},
        )
    }
    downloads = {
        "structure": Operation(
            "Resolve metadata, select the exact accession, then download coordinates or PAE.",
            StructureInput,
            {"accession": "P00533", "format": "cif"},
        )
    }

    def query(self, operation, params):
        response = self.request(
            "GET", f"https://alphafold.ebi.ac.uk/api/prediction/{params.accession}"
        )
        if not isinstance(response.data, list):
            raise MolQuarryError(
                "invalid_response", "Expected AlphaFold model list", source=self.id
            )
        return Page(
            response.data,
            [response.provenance],
            warnings=[
                "Predicted protein structures are not experimental protein–ligand complexes; "
                "retain pocket confidence and model version."
            ],
        )

    def plan(self, operation, params):
        page = self.query("prediction", AccessionInput(accession=params.accession))
        models = page.records
        if params.model_id:
            models = [
                m for m in models if (m.get("modelEntityId") or m.get("entryId")) == params.model_id
            ]
        elif all("uniprotAccession" in model for model in models):
            # Canonical lookups also return isoforms; match the requested sequence accession.
            # Multiple fragments of that accession still require an explicit model_id.
            models = [m for m in models if m["uniprotAccession"] == params.accession]
        if not models:
            raise MolQuarryError("not_found", "No matching AlphaFold model", source=self.id)
        if len(models) > 1:
            raise MolQuarryError(
                "ambiguous_model",
                "Multiple models: specify model_id",
                source=self.id,
                details={"model_ids": [m.get("modelEntityId") or m.get("entryId") for m in models]},
            )
        model = models[0]
        key = {"cif": "cifUrl", "pdb": "pdbUrl", "pae": "paeDocUrl"}[params.format]
        url = model.get(key)
        if not url:
            raise MolQuarryError(
                "not_found", f"This model has no {params.format} artifact", source=self.id
            )
        version = model.get("latestVersion")
        return self.make_plan(
            operation,
            params,
            url=url,
            filename=PurePosixPath(urlparse(url).path).name,
            format="json" if params.format == "pae" else params.format,
            source_version=str(version) if version is not None else None,
            provenance=page.provenance,
            notes=page.warnings,
        )
