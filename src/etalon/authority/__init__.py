"""Spending is reachable only through the check, because the check's output is the argument.

ADR 0006 says a guard on the path nobody takes is not a guard. ETALON's MCP server exposes seven
free tools, one cheap one, and one no model may complete -- and nothing that spends. So every
refusal it produces is advice offered beside an action it has no relationship with, and the
governance rests on a model choosing to be governed.

This package makes the relationship structural: :func:`authorize` runs the preflight and returns
tokens, the expensive stage requires one per row, and a token is bound to a digest of the exact
record so that checking one thing and building another is caught as well.
"""

from etalon.authority.grant import (
    DEFAULT_LIFETIME_HOURS,
    Authorized,
    NotAuthorized,
    SpendAuthorization,
    authorize,
    record_digest,
    require,
    unauthorized,
)

__all__ = [
    "DEFAULT_LIFETIME_HOURS",
    "Authorized",
    "NotAuthorized",
    "SpendAuthorization",
    "authorize",
    "record_digest",
    "require",
    "unauthorized",
]
