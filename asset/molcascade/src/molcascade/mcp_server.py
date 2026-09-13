#!/usr/bin/env python3
"""MolCascade MCP server — entry point.

Exposes MolCascade's screening, auditing and reporting capabilities as MCP tools
an agent can call.  The tools, their grouping and the two properties that make
this surface safe to drive from a loop are documented in
:mod:`molcascade.mcp`; read ``molcascade://overview`` before the first call in a
session.

Usage::

    # Browser-based debug UI
    mcp dev src/molcascade/mcp_server.py

    # Register with Claude Code (installed package)
    claude mcp add --transport stdio molcascade -- python -m molcascade.mcp_server

    # Register with Claude Code (editable checkout, explicit interpreter)
    claude mcp add --transport stdio molcascade -- \\
        /path/to/env/bin/python -m molcascade.mcp_server

Name the interpreter explicitly.  MolCascade's backends live in the environment
it was installed into, and a bare ``python`` resolves to whatever is first on the
client's PATH -- which is how a server comes up reporting every backend
unavailable.

stdout belongs to JSON-RPC under this transport.  Every tool redirects library
output to stderr before calling anything, so progress and warnings remain visible
in the server log without corrupting the protocol.
"""

from __future__ import annotations


def main() -> None:
    from molcascade.mcp import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
