from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from ..models import DownloadPlan, InputModel, Provenance


class NoParams(InputModel):
    pass


class PageInput(InputModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class Operation:
    description: str
    input_model: type[InputModel]
    example: dict[str, Any]
    requires_env: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "example": self.example,
            "requires_env": list(self.requires_env),
        }


@dataclass
class Page:
    records: list[dict[str, Any]]
    provenance: list[Provenance] = field(default_factory=list)
    total: int | None = None
    next_parameters: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)


class Provider:
    id: str
    operations: dict[str, Operation] = {}
    downloads: dict[str, Operation] = {}
    download_hosts: frozenset[str] = frozenset()

    def __init__(self, http, spec):
        self.http = http
        self.spec = spec

    def request(self, method: str, url: str, **kwargs):
        return self.http.json(self.spec, method, url, **kwargs)

    def query(self, operation: str, params: InputModel) -> Page:
        raise NotImplementedError

    def plan(self, operation: str, params: InputModel) -> DownloadPlan:
        raise NotImplementedError

    def download_headers(self, url: str) -> dict[str, str]:
        return {}

    def make_plan(self, operation: str, params: InputModel, **kwargs) -> DownloadPlan:
        return DownloadPlan(
            source=self.id,
            operation=operation,
            parameters=params.model_dump(exclude_none=True),
            license_url=self.spec.license_url,
            **kwargs,
        )
