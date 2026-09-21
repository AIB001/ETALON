from typing import Literal
from urllib.parse import parse_qs, urlparse

from pydantic import Field

from ..errors import MolQuarryError
from ..models import InputModel
from .base import Operation, Page, Provider

BASE = "https://mcule.com/api/v1"


class FilesInput(InputModel):
    dataset_id: int | None = Field(default=None, gt=0)
    page: int = Field(default=1, ge=1)


class CompoundInput(InputModel):
    mcule_id: str = Field(pattern=r"^MCULE-[0-9]+$")


class LookupInput(InputModel):
    inchikey: str = Field(pattern=r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")


class DatasetInput(InputModel):
    dataset_id: int = Field(gt=0)
    file_type: Literal["smi.gz", "sdf.gz", "csv.gz"] = "smi.gz"


class Mcule(Provider):
    id = "mcule"
    download_hosts = frozenset({"dl.mcule.com", "mcule.s3.amazonaws.com"})
    operations = {
        "database_files": Operation(
            "Discover public catalog files, release dates, sizes and SHA256.",
            FilesInput,
            {"dataset_id": 1},
        ),
        "compound": Operation(
            "Public compound identity and properties; not live pricing.",
            CompoundInput,
            {"mcule_id": "MCULE-9380369173"},
        ),
        "lookup": Operation(
            "Find Mcule IDs from an exact full InChIKey.",
            LookupInput,
            {"inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"},
        ),
    }
    downloads = {
        "dataset": Operation(
            "Resolve a public catalog artifact with upstream checksum.",
            DatasetInput,
            {"dataset_id": 1, "file_type": "smi.gz"},
        )
    }

    def query(self, operation, params):
        if operation == "compound":
            response = self.request("GET", f"{BASE}/compound/{params.mcule_id}/")
            return Page([response.data], [response.provenance])
        if operation == "lookup":
            response = self.request("GET", f"{BASE}/lookup/inchikey/{params.inchikey}")
            return Page(response.data["results"], [response.provenance])
        path = f"database-files/{params.dataset_id}/" if params.dataset_id else "database-files/"
        response = self.request(
            "GET", f"{BASE}/{path}", params={"page": params.page} if not params.dataset_id else None
        )
        data = response.data
        # The live detail endpoint is a single object; older docs show a results envelope.
        if "results" in data:
            rows = data["results"]
        elif "id" in data and "files" in data:
            rows = [data]
        else:
            raise MolQuarryError(
                "invalid_response", "Unrecognized Mcule catalog response", source=self.id
            )
        next_params = None
        if data.get("next"):
            page_number = int(parse_qs(urlparse(data["next"]).query)["page"][0])
            next_params = {**params.model_dump(exclude_none=True), "page": page_number}
        return Page(
            rows,
            [response.provenance],
            data.get("count", len(rows)),
            next_params,
            [
                "A catalog entry is not a live offer; stock, regional delivery and prices need "
                "a current supplier confirmation. This adapter uses only public endpoints."
            ],
        )

    def plan(self, operation, params):
        page = self.query("database_files", FilesInput(dataset_id=params.dataset_id))
        if len(page.records) != 1:
            raise MolQuarryError("not_found", "Expected one Mcule catalog", source=self.id)
        catalog = page.records[0]
        files = [f for f in catalog["files"] if f["file_type"] == params.file_type]
        if len(files) != 1:
            raise MolQuarryError(
                "not_found", "Requested catalog format is not uniquely available", source=self.id
            )
        artifact = files[0]
        return self.make_plan(
            operation,
            params,
            url=artifact["download_url"],
            filename=artifact["filename"],
            format=params.file_type,
            source_version=catalog.get("last_updated"),
            expected_sha256=artifact.get("sha256_checksum"),
            estimated_bytes=int(artifact["size_mb"] * 1_000_000)
            if artifact.get("size_mb")
            else None,
            provenance=page.provenance,
            notes=page.warnings,
        )
