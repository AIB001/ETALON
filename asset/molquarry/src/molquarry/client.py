import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import local_catalog
from .catalog import CATEGORIES, SOURCES, catalog_entries
from .downloads import download, validate_url
from .errors import MolQuarryError
from .http import HttpClient
from .models import DownloadPlan, QueryResult
from .providers import PROVIDERS


class MolQuarry:
    def __init__(
        self, *, home: str | Path | None = None, cache: bool = True, http: HttpClient | None = None
    ):
        self.home = Path(home or os.environ.get("MOLQUARRY_HOME", ".molquarry")).resolve()
        self.http = http or HttpClient(cache_dir=self.home / "cache" if cache else None)
        self.providers = {key: cls(self.http, SOURCES[key]) for key, cls in PROVIDERS.items()}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.http.close()

    def sources(
        self,
        *,
        category: str | None = None,
        implemented_only: bool = False,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        if category is not None and category not in CATEGORIES:
            raise MolQuarryError(
                "unknown_category",
                f"Unknown category: {category}",
                details={"categories": CATEGORIES},
            )
        entries = catalog_entries()
        if category:
            entries = [s for s in entries if category in s["categories"]]
        if implemented_only:
            entries = [s for s in entries if s["implementation"] == "implemented"]
        if query:
            query = query.casefold()
            entries = [s for s in entries if query in json.dumps(s, ensure_ascii=False).casefold()]
        for entry in entries:
            provider = self.providers.get(entry["id"])
            entry["query_operations"] = list(provider.operations) if provider else []
            entry["download_operations"] = list(provider.downloads) if provider else []
        return entries

    def describe(self, source: str) -> dict[str, Any]:
        entries = [s for s in self.sources() if s["id"] == source]
        if not entries:
            raise MolQuarryError("unknown_source", f"Unknown source: {source}")
        entry = entries[0]
        entry["credentials_configured"] = all(
            os.environ.get(key) for key in entry["credential_env"]
        )
        entry["supports_local_catalog"] = True
        provider = self.providers.get(source)
        entry["operations"] = (
            {k: v.describe() for k, v in provider.operations.items()} if provider else {}
        )
        entry["downloads"] = (
            {k: v.describe() for k, v in provider.downloads.items()} if provider else {}
        )
        return entry

    def import_catalog(self, source: str, path: str | Path, **options):
        self._provider(source)
        try:
            inputs = local_catalog.ImportOptions.model_validate(options)
            return local_catalog.import_catalog(self.home, SOURCES[source], Path(path), inputs)
        except ValidationError as exc:
            raise MolQuarryError("invalid_parameters", "Invalid catalog import options") from exc
        except (OSError, sqlite3.Error) as exc:
            raise MolQuarryError("filesystem_error", "Unable to publish local catalog") from exc

    def local_search(self, **parameters):
        try:
            return local_catalog.local_search(
                self.home, local_catalog.LocalSearch.model_validate(parameters)
            )
        except ValidationError as exc:
            raise MolQuarryError("invalid_parameters", "Invalid local search parameters") from exc
        except (OSError, sqlite3.Error) as exc:
            raise MolQuarryError("filesystem_error", "Unable to read local catalog") from exc

    def local_catalogs(self):
        try:
            return {"ok": True, "catalogs": local_catalog.list_catalogs(self.home)}
        except (OSError, sqlite3.Error) as exc:
            raise MolQuarryError("filesystem_error", "Unable to list local catalogs") from exc

    def _provider(self, source):
        if source not in self.providers:
            info = self.describe(source)
            raise MolQuarryError(
                "not_implemented",
                f"{info['name']} is in the roadmap, not yet callable",
                source=source,
            )
        return self.providers[source]

    @staticmethod
    def _validate(provider, operation, parameters, *, is_download=False):
        operations = provider.downloads if is_download else provider.operations
        if operation not in operations:
            raise MolQuarryError(
                "unknown_operation",
                f"Unknown operation: {operation}",
                source=provider.id,
                details={"available": list(operations)},
            )
        try:
            return operations[operation].input_model.model_validate(parameters)
        except ValidationError as exc:
            raise MolQuarryError(
                "invalid_parameters",
                "Parameters do not match this operation's schema",
                source=provider.id,
                details={
                    "validation": exc.errors(
                        include_url=False, include_context=False, include_input=False
                    )
                },
            ) from exc

    def query(self, source: str, operation: str, **parameters) -> QueryResult:
        provider = self._provider(source)
        params = self._validate(provider, operation, parameters)
        try:
            page = provider.query(operation, params)
            return QueryResult(
                source=source,
                operation=operation,
                parameters=params.model_dump(exclude_none=True),
                records=page.records,
                returned=len(page.records),
                total=page.total,
                next_parameters=page.next_parameters,
                provenance=page.provenance,
                warnings=page.warnings,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MolQuarryError(
                "invalid_response",
                f"{source} response no longer matches its adapter schema",
                source=source,
            ) from exc

    def iter_pages(self, source: str, operation: str, *, max_pages: int = 10, **parameters):
        """A bounded iterator; the last page retains its continuation when the bound is reached."""
        if max_pages < 1:
            raise MolQuarryError("invalid_parameters", "max_pages must be positive")
        seen = set()
        for _ in range(max_pages):
            key = json.dumps(parameters, sort_keys=True)
            if key in seen:
                raise MolQuarryError("pagination_loop", "Source returned a repeated continuation")
            seen.add(key)
            result = self.query(source, operation, **parameters)
            yield result
            if result.next_parameters is None:
                break
            parameters = result.next_parameters

    def plan_download(self, source: str, operation: str, **parameters) -> DownloadPlan:
        provider = self._provider(source)
        params = self._validate(provider, operation, parameters, is_download=True)
        try:
            plan = provider.plan(operation, params)
            validate_url(plan.url, provider.download_hosts)
            return plan
        except (KeyError, TypeError, ValueError) as exc:
            raise MolQuarryError(
                "invalid_response",
                "Source download metadata does not match its schema",
                source=source,
            ) from exc

    def download(
        self,
        plan: DownloadPlan,
        *,
        output_dir: str | Path | None = None,
        max_bytes: int = 100 * 1024 * 1024,
    ):
        provider = self._provider(plan.source)
        self._validate(provider, plan.operation, plan.parameters, is_download=True)
        try:
            return download(
                self.http,
                provider,
                plan,
                Path(output_dir)
                if output_dir is not None
                else self.home / "downloads" / plan.source,
                max_bytes,
            )
        except OSError as exc:
            raise MolQuarryError("filesystem_error", str(exc), source=plan.source) from exc
