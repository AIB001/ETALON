"""Small shared contracts; source response envelopes stay in their adapters."""

import os
from typing import Annotated

from pydantic import Field

from ..errors import MolQuarryError
from ..models import InputModel
from .base import Page, PageInput

Text = Annotated[str, Field(min_length=1, max_length=2000)]
Accession = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$")]


class Search(PageInput):
    query: Text


class Identifier(InputModel):
    identifier: Accession


def credential(name: str, source: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MolQuarryError(
            "authentication_required",
            f"Configure {name} in the server environment",
            source=source,
            details={"required_env": [name]},
        )
    if "\n" in value or "\r" in value:
        raise MolQuarryError("invalid_credentials", "Credential contains a newline", source=source)
    return value


def records(data):
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return data
    raise ValueError("Expected object or object list")


def local_page(items, response, params, *, warnings=None):
    """Paginate a documented unpaginated response; never imply server-side limiting."""
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Expected a record list")
    start, limit = params.offset, params.limit
    return Page(
        items[start : start + limit],
        [response.provenance],
        len(items),
        {**params.model_dump(), "offset": start + limit} if start + limit < len(items) else None,
        warnings
        or [
            (
                "Upstream response was fetched in full (bounded by HTTP byte budget); pagination "
                "is local."
            )
        ],
    )


def offset_page(items, response, params, total):
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Expected a record list")
    more = params.offset + len(items) < total if total is not None else len(items) == params.limit
    return Page(
        items,
        [response.provenance],
        total,
        {**params.model_dump(), "offset": params.offset + params.limit} if more and items else None,
    )
