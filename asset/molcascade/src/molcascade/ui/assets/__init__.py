"""Vendored first-party assets for the self-contained offline UI."""

from __future__ import annotations

import base64
from functools import lru_cache
from importlib.resources import files

__all__ = ["png_data_uri"]


@lru_cache(maxsize=4)
def png_data_uri(name: str) -> str:
    """Read a bundled PNG and return it as a ``data:`` URI.

    Every page this package renders is one file that has to survive being
    emailed, dropped on a share or opened from a USB stick, so an image can only
    travel inside the document -- a ``src`` pointing at a sibling file is a
    broken image the moment the HTML moves.  Each stylesheet's
    Content-Security-Policy already allows ``img-src data:`` and nothing else,
    which is the same rule stated from the other side.

    Cached because the base64 of the wordmark is ~42 KB and a served builder
    re-renders the document on every request.
    """

    payload = files(__name__).joinpath(name).read_bytes()
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
