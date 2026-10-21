"""Spending is reachable only through the check, because the check's output is the argument.

ADR 0006 says a guard on the path nobody takes is not a guard, and this package was written when
the MCP server exposed nine tools, none of which spent: every refusal it produced was advice
offered beside an action it had no relationship with, and the governance rested on a model choosing
to be governed.

That sentence was in the present tense and is no longer true of the server. Measured: 57 tools, 29
free, 22 cheap, 5 that spend and one no model may complete. The argument below is unaffected and
the premise has to be stated as history -- and one thing it describes is still exactly true of the
expensive stage, which is the path this package guards: nothing on the tool surface accepts a spend
token, so ``require`` is reached only from Python.

This package makes the relationship structural: :func:`authorize` runs the preflight and returns
tokens, the expensive stage requires one per row, and a token is bound to a digest of the exact
record so that checking one thing and building another is caught as well.

:mod:`etalon.authority.gate` is the same construction for the regime with no expensive stage, where
the irreversible act is not a wasted GPU-hour but a miscalibrated threshold applied a million times.
"""

from etalon.authority.gate import (
    DEFAULT_GATE_LIFETIME_HOURS,
    GateAuthorization,
    authorize_gate,
    calibration_digest,
    require_gate,
    unauthorized_gate,
)
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
    "DEFAULT_GATE_LIFETIME_HOURS",
    "DEFAULT_LIFETIME_HOURS",
    "Authorized",
    "GateAuthorization",
    "NotAuthorized",
    "SpendAuthorization",
    "authorize",
    "authorize_gate",
    "calibration_digest",
    "record_digest",
    "require",
    "require_gate",
    "unauthorized",
    "unauthorized_gate",
]
