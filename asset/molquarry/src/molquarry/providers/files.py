"""Discover artifacts on official pages. No crawling, script execution, or login bypass."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from pydantic import Field, field_validator

from ..downloads import validate_url
from ..errors import MolQuarryError
from ..models import InputModel
from .base import NoParams, Operation, Page, PageInput, Provider
from .common import local_page

FORMATS = (
    "tar.gz",
    "tsv.gz",
    "csv.gz",
    "sdf.gz",
    "smi.gz",
    "fasta.gz",
    "txt.gz",
    "sql.gz",
    "json.gz",
    "gz",
    "tar.bz2",
    "bz2",
    "xz",
    "zip",
    "parquet",
    "sdf",
    "mol",
    "csv",
    "tsv",
    "smi",
    "smiles",
    "txt",
    "fasta",
    "fa",
    "json",
    "xml",
    "obo",
    "owl",
    "ttl",
    "sql",
    "xlsx",
    "xls",
    "tgz",
    "h5",
    "hdf5",
)


def file_format(name):
    return next((f for f in FORMATS if name.lower().endswith("." + f)), None)


def safe_name(name):
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", unquote(name))[:180]
    return name if name and name[0].isalnum() else "data_" + name


class FileList(PageInput):
    root: str = Field(default="default", pattern=r"^[a-z0-9_]+$")
    path: str = Field(default="", max_length=500)
    query: str = Field(default="", max_length=200)

    @field_validator("path")
    @classmethod
    def relative_directory(cls, v):
        if v and (
            v.startswith("/")
            or not v.endswith("/")
            or any(x in v for x in ("..", "%", "?", "#", "\\", ":", "\x00"))
        ):
            raise ValueError(
                "path must be a plain relative directory ending in /, without traversal"
            )
        return v


class Artifact(InputModel):
    root: str = Field(default="default", pattern=r"^[a-z0-9_]+$")
    path: str = ""
    url: str = Field(min_length=10, max_length=2000)

    _validate_path = field_validator("path")(FileList.relative_directory.__func__)


@dataclass(frozen=True)
class BulkRoot:
    url: str
    hosts: frozenset[str]
    directory: bool = False
    notes: str = "Use the source's data license; availability does not grant redistribution rights."


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and key in {"href", "data-href", "data-url", "data-download"}:
                self.links.append(value)


def discover(provider, roots, params):
    if params.root not in roots:
        raise MolQuarryError(
            "invalid_parameters", "Unknown file root", details={"roots": list(roots)}
        )
    root = roots[params.root]
    if params.path and not root.directory:
        raise MolQuarryError(
            "invalid_parameters", "This source uses a fixed official download page"
        )
    page_url = root.url + params.path if root.directory else root.url
    r = provider.http.text(
        provider.spec, "GET", page_url, headers={"Accept": "text/html"}, redirect_hosts=root.hosts
    )
    parser = Links()
    parser.feed(r.data)
    rows, seen = [], set()
    for link in parser.links:
        url = urljoin(r.provenance.url, link)
        parsed = urlparse(url)
        if (
            parsed.fragment
            or parsed.query
            and not any(k in parse_qs(parsed.query) for k in ("download_file", "path"))
        ):
            continue
        # BindingDB and Life Chemicals link the actual filename via a documented download link.
        # Keep the download endpoint except BindingDB's explicit public download_file target.
        named = parse_qs(parsed.query).get("download_file")
        if provider.id == "bindingdb" and named:
            url = urljoin(url, named[0])
            parsed = urlparse(url)
        try:
            validate_url(url, root.hosts)
        except MolQuarryError:
            continue
        name = PurePosixPath(parsed.path.rstrip("/")).name
        if "path" in parse_qs(parsed.query):
            name = PurePosixPath(parse_qs(parsed.query)["path"][0]).name
        fmt = file_format(name)
        if parsed.path.endswith("/") and root.directory:
            base_path = urlparse(root.url).path
            if parsed.netloc != urlparse(root.url).netloc or not parsed.path.startswith(base_path):
                continue
            rel = parsed.path[len(base_path) :]
            if not rel or rel == params.path or ".." in rel or not rel.startswith(params.path):
                continue
            row = {"kind": "directory", "path": rel, "url": url, "root": params.root}
        elif fmt:
            row = {
                "kind": "file",
                "url": url,
                "filename": safe_name(name),
                "format": fmt,
                "root": params.root,
                "path": params.path,
            }
        else:
            continue
        if url not in seen:
            rows.append(row)
            seen.add(url)
    if not rows:
        raise MolQuarryError(
            "no_download_links",
            "No supported public file links found; page may require login or JavaScript",
            source=provider.id,
            details={"page": page_url, "docs_url": provider.spec.docs_url},
        )
    return rows, r


def with_access(cls: type[Provider], roots: dict[str, BulkRoot] | None = None):
    """Compose common access metadata and optional verified-page discovery with an adapter."""
    roots = roots or {}
    parent = cls

    class Accessible(parent):
        operations = {
            **parent.operations,
            "resources": Operation(
                (
                    "Offline access instructions, official pages and license requirements; not "
                    "database results"
                ),
                NoParams,
                {},
            ),
        }
        downloads = dict(parent.downloads)
        download_hosts = parent.download_hosts | frozenset(
            h for r in roots.values() for h in r.hosts
        )
        if roots:
            operations["files"] = Operation(
                (
                    "Discover files/directories on an official page. Paths are relative to a "
                    "declared root; pagination is local"
                ),
                FileList,
                {},
            )
            downloads["artifact"] = Operation(
                "Resolve one file returned by files; requires the same root/path and exact URL",
                Artifact,
                {"url": "https://example.invalid/select-a-url-returned-by-files.csv"},
            )

        def query(self, operation, params):
            if operation == "resources":
                return Page(
                    [
                        {
                            "homepage": self.spec.homepage,
                            "docs_url": self.spec.docs_url,
                            "license_url": self.spec.license_url,
                            "license_notes": self.spec.license_notes,
                            "auth": self.spec.auth,
                            "file_roots": {
                                k: {"url": v.url, "directory": v.directory}
                                for k, v in roots.items()
                            },
                            "bulk_entrypoints": self.spec.bulk_entrypoints,
                            "local_import": (
                                "Import an authorized CSV/TSV/SDF catalog using import-catalog; "
                                "search it with local-search."
                            ),
                        }
                    ],
                    warnings=["Access instructions only; no live database query was performed."],
                )
            if operation == "files":
                rows, response = discover(self, roots, params)
                if params.query:
                    rows = [r for r in rows if params.query.casefold() in r["url"].casefold()]
                return local_page(rows, response, params)
            return super().query(operation, params)

        def plan(self, operation, params):
            if operation != "artifact":
                return super().plan(operation, params)
            validate_url(params.url, self.download_hosts)
            rows, r = discover(self, roots, params)
            row = next((x for x in rows if x["kind"] == "file" and x["url"] == params.url), None)
            if row is None:
                raise MolQuarryError(
                    "artifact_not_listed",
                    "URL is not in the selected official file listing",
                    source=self.id,
                )
            return self.make_plan(
                operation,
                params,
                url=row["url"],
                filename=row["filename"],
                format=row["format"],
                provenance=[r.provenance],
                notes=[
                    roots[params.root].notes,
                    (
                        "File size and upstream SHA256 are not published by this HTML listing; "
                        "download is still byte-budgeted and hashed."
                    ),
                ],
            )

    Accessible.__name__ = parent.__name__
    return Accessible
