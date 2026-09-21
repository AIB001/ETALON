"""Versioned dataset metadata and artifact resolution, without heavy ML dependencies."""

import ast
import os
from urllib.parse import parse_qs, quote, urlparse

from pydantic import Field, field_validator

from ..errors import MolQuarryError
from ..models import InputModel
from .base import Operation, Page, Provider
from .common import Accession, Search, local_page
from .files import file_format, safe_name


class HubPath(InputModel):
    revision: str = Field(default="main", pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    path: str = Field(default="", max_length=500)

    @field_validator("path")
    @classmethod
    def path_scope(cls, v):
        if v and (
            not v.startswith("data/")
            and v != "data"
            or any(s in v for s in ("..", "%", "\\", "?", "#", ":"))
        ):
            raise ValueError("Only repository data/ paths without traversal are supported")
        return v


class HubFiles(HubPath):
    cursor: str | None = Field(default=None, max_length=3000)
    limit: int = Field(default=20, ge=1, le=100)


class ORD(Provider):
    id = "ord"
    repo = "open-reaction-database/ord-data"
    operations = {
        "tree": Operation(
            (
                "List the official ORD Hugging Face mirror, pinned to a commit; navigate data/ "
                "directories"
            ),
            HubFiles,
            {"path": "data/00", "limit": 5},
        )
    }
    downloads = {
        "dataset": Operation(
            "Resolve a Parquet file at an exact ORD mirror commit",
            HubPath,
            {"path": "data/00/select-a-parquet-path-from-tree.parquet"},
        )
    }
    download_hosts = frozenset(
        {
            "huggingface.co",
            "cdn-lfs.huggingface.co",
            "cdn-lfs.hf.co",
            "cdn-lfs-us-1.hf.co",
            "cas-bridge.xethub.hf.co",
            "us.aws.cdn.hf.co",
            "us.aws.cdn.huggingface.co",
        }
    )

    def revision(self, params):
        r = self.request(
            "GET", f"https://huggingface.co/api/datasets/{self.repo}/revision/{params.revision}"
        )
        return r.data["sha"], r

    def query(self, operation, params):
        sha, meta = self.revision(params)
        r = self.request(
            "GET",
            f"https://huggingface.co/api/datasets/{self.repo}/tree/{sha}/{params.path}".rstrip("/"),
            params={"limit": params.limit, "cursor": params.cursor},
        )
        if not isinstance(r.data, list):
            raise ValueError("Missing HF tree list")
        r.provenance.source_version = sha
        import re

        match = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("link", ""))
        cursor = (
            parse_qs(urlparse(match.group(1)).query).get("cursor", [None])[0] if match else None
        )
        return Page(
            r.data,
            [meta.provenance, r.provenance],
            next_parameters={**params.model_dump(), "revision": sha, "cursor": cursor}
            if cursor
            else None,
        )

    def plan(self, operation, params):
        if not params.path.endswith(".parquet"):
            raise MolQuarryError("invalid_parameters", "Select a data/*.parquet file from tree")
        sha, meta = self.revision(params)
        r = self.request(
            "POST",
            f"https://huggingface.co/api/datasets/{self.repo}/paths-info/{sha}",
            body={"paths": [params.path]},
        )
        if not r.data or r.data[0]["path"] != params.path or r.data[0]["type"] != "file":
            raise MolQuarryError("not_found", "ORD file not found at requested commit")
        entry = r.data[0]
        return self.make_plan(
            operation,
            params,
            url=f"https://huggingface.co/datasets/{self.repo}/resolve/{sha}/{params.path}",
            filename=safe_name(params.path.rsplit("/", 1)[-1]),
            format="parquet",
            estimated_bytes=entry["size"],
            expected_sha256=entry.get("lfs", {}).get("oid"),
            source_version=sha,
            provenance=[meta.provenance, r.provenance],
            notes=[
                (
                    "Use ord-schema to decode the serialized Protocol Buffer column; no reaction "
                    "ETL is performed."
                )
            ],
        )


class TDCName(InputModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")


class TDC(Provider):
    id = "tdc"
    operations = {
        "datasets": Operation(
            (
                "Search TDC's official dataset-to-file-ID registry; safe literal parsing, never "
                "executes remote code"
            ),
            Search,
            {"query": "caco2"},
        ),
        "dataset": Operation(
            "Resolve a TDC dataset's original Dataverse file metadata",
            TDCName,
            {"name": "caco2_wang"},
        ),
    }
    downloads = {
        "dataset": Operation(
            "Download the original dataset behind a TDC task without installing the ML stack",
            TDCName,
            {"name": "caco2_wang"},
        )
    }
    download_hosts = frozenset(
        {
            "dataverse.harvard.edu",
            "dvn-cloud-iqss.s3.amazonaws.com",
            "dvn-cloud-iqss.s3.us-east-1.amazonaws.com",
        }
    )

    def registry(self):
        r = self.http.text(
            self.spec,
            "GET",
            "https://raw.githubusercontent.com/mims-harvard/TDC/main/tdc/metadata.py",
        )
        data = {}
        try:
            for node in ast.parse(r.data).body:
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                ):
                    name = node.targets[0].id
                    if name in {"name2id", "name2type"}:
                        data[name] = ast.literal_eval(node.value)
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise MolQuarryError(
                "invalid_response", "TDC metadata is no longer a supported literal registry"
            ) from exc
        rows = [
            {"name": name, "file_id": id_, "format": data["name2type"][name]}
            for name, id_ in data["name2id"].items()
            if isinstance(id_, int) and name in data["name2type"]
        ]
        r.provenance.source_version = "metadata-sha256:" + r.provenance.response_sha256
        return rows, r

    def metadata(self, params):
        rows, registry = self.registry()
        row = next((x for x in rows if x["name"] == params.name.casefold()), None)
        if row is None:
            raise MolQuarryError("not_found", "Dataset not in supported TDC single-file registry")
        r = self.request(
            "GET", f"https://dataverse.harvard.edu/api/files/{row['file_id']}/metadata"
        )
        return row, r, registry

    def query(self, operation, params):
        if operation == "datasets":
            rows, r = self.registry()
            page = local_page([x for x in rows if params.query.casefold() in x["name"]], r, params)
            page.warnings.append(
                "Only single-file registry entries; inspect the underlying dataset's license "
                "separately."
            )
            return page
        row, r, registry = self.metadata(params)
        return Page([{**row, "metadata": r.data}], [registry.provenance, r.provenance])

    def plan(self, operation, params):
        row, r, registry = self.metadata(params)
        if r.data.get("restricted") is True:
            raise MolQuarryError("access_denied", "This Dataverse file is restricted")
        fmt = {"tab": "tsv"}.get(row["format"], row["format"]).lower()
        if file_format("data." + fmt) is None:
            raise MolQuarryError(
                "unsupported_format",
                "This TDC dataset container is not supported by the downloader",
            )
        return self.make_plan(
            operation,
            params,
            url=f"https://dataverse.harvard.edu/api/access/datafile/{row['file_id']}",
            filename=f"{row['name']}.{fmt}",
            format=fmt,
            source_version=f"dataverse-file:{row['file_id']}",
            provenance=[registry.provenance, r.provenance],
            notes=[
                (
                    "Original Dataverse file ID is fixed. Source license must be checked "
                    "independently of TDC code license."
                ),
                "Tabular files use Dataverse's default TSV representation, which may differ "
                "from the originally uploaded file format.",
            ],
        )


class FigshareArticle(InputModel):
    article_id: int = Field(ge=1)
    version: int | None = Field(default=None, ge=1)


class FigshareFile(FigshareArticle):
    file_id: int = Field(ge=1)


class DepMap(Provider):
    id = "depmap"
    operations = {
        "release": Operation(
            "Official Broad DepMap Figshare article metadata, release files and license",
            FigshareArticle,
            {"article_id": 27993248},
        )
    }
    downloads = {
        "file": Operation(
            "Resolve a file listed by an official Broad DepMap release",
            FigshareFile,
            {"article_id": 27993248, "file_id": 51064856},
        )
    }
    download_hosts = frozenset(
        {
            "ndownloader.figshare.com",
            "api.figshare.com",
            "figshare.com",
            "s3-eu-west-1.amazonaws.com",
            "s3.eu-west-1.amazonaws.com",
        }
    )

    def release(self, params):
        suffix = f"/versions/{params.version}" if params.version else ""
        r = self.request("GET", f"https://api.figshare.com/v2/articles/{params.article_id}{suffix}")
        # Figshare hosts unrelated datasets too: verify the official Broad DepMap depositor.
        if not any(a.get("id") in {5514062, 17476659} for a in r.data["authors"]):
            raise MolQuarryError(
                "unverified_source",
                "Article is not deposited by the official Broad DepMap author",
                source=self.id,
            )
        r.provenance.source_version = str(r.data["version"])
        return r

    def query(self, operation, params):
        r = self.release(params)
        return Page([r.data], [r.provenance])

    def plan(self, operation, params):
        r = self.release(params)
        f = next((x for x in r.data["files"] if x["id"] == params.file_id), None)
        if not f:
            raise MolQuarryError("not_found", "File is not in the selected DepMap article version")
        fmt = file_format(f["name"])
        if fmt is None:
            raise MolQuarryError("unsupported_format", "Unsupported DepMap file container")
        plan = self.make_plan(
            operation,
            params,
            url=f["download_url"],
            filename=safe_name(f["name"]),
            format=fmt,
            estimated_bytes=f["size"],
            source_version=str(r.data["version"]),
            provenance=[r.provenance],
            notes=[f"Article DOI: {r.data.get('doi')}; upstream MD5: {f.get('computed_md5')}"],
        )
        plan.license_url = r.data["license"]["url"]
        return plan


class PlinderFiles(InputModel):
    release: str = Field(default="2024-06", pattern=r"^\d{4}-\d{2}$")
    iteration: Accession = "v2"
    prefix: str = Field(default="index/", pattern=r"^[A-Za-z0-9_/-]*$")
    page_token: str | None = Field(default=None, max_length=3000)
    limit: int = Field(default=20, ge=1, le=100)


class PlinderFile(InputModel):
    object_name: str = Field(
        pattern=r"^\d{4}-\d{2}/[A-Za-z0-9_/-]+\.(parquet|zip)$", max_length=500
    )


class PLINDER(Provider):
    id = "plinder"
    operations = {
        "objects": Operation(
            (
                "List release objects through the GCS JSON API; optional "
                "PLINDER_GCP_ACCESS_TOKEN when bucket policy requires it"
            ),
            PlinderFiles,
            {},
        )
    }
    downloads = {
        "object": Operation(
            "Resolve GCS object metadata/generation before download",
            PlinderFile,
            {"object_name": "2024-06/v2/index/annotation_table.parquet"},
        )
    }
    download_hosts = frozenset({"storage.googleapis.com"})

    def gcs(self, path="", **kwargs):
        token = os.environ.get("PLINDER_GCP_ACCESS_TOKEN")
        return self.request(
            "GET",
            "https://storage.googleapis.com/storage/v1/b/plinder/o" + path,
            headers={"Authorization": "Bearer " + token} if token else None,
            private=bool(token),
            **kwargs,
        )

    def query(self, operation, params):
        prefix = f"{params.release}/{params.iteration}/{params.prefix}"
        r = self.gcs(
            params={
                "prefix": prefix,
                "delimiter": "/",
                "maxResults": params.limit,
                "pageToken": params.page_token,
            }
        )
        rows = [
            {"kind": "directory", "prefix": p} for p in r.data.get("prefixes", [])
        ] + r.data.get("items", [])
        if r.data.get("kind") != "storage#objects":
            raise ValueError("Not a GCS object listing")
        return Page(
            rows,
            [r.provenance],
            next_parameters={**params.model_dump(), "page_token": r.data["nextPageToken"]}
            if r.data.get("nextPageToken")
            else None,
        )

    def plan(self, operation, params):
        r = self.gcs("/" + quote(params.object_name, safe=""))
        item = r.data
        url = f"https://storage.googleapis.com/plinder/{params.object_name}?generation={item['generation']}"
        return self.make_plan(
            operation,
            params,
            url=url,
            filename=safe_name(params.object_name.replace("/", "_")),
            format=file_format(params.object_name),
            estimated_bytes=int(item["size"]),
            source_version=item["generation"],
            provenance=[r.provenance],
            notes=[
                (
                    "PLINDER release + object generation pinned. Bucket access policy may "
                    "require GCP credentials."
                )
            ],
        )

    def download_headers(self, url):
        token = os.environ.get("PLINDER_GCP_ACCESS_TOKEN")
        return (
            {"Authorization": "Bearer " + token}
            if token and urlparse(url).hostname == "storage.googleapis.com"
            else {}
        )
