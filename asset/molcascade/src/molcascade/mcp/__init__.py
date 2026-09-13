"""MolCascade's MCP server — the package's interface for an agent.

MolCascade is an auditable, modular, hierarchical molecular screening tool.  A
configuration describes a funnel; running it narrows a library from millions of
molecules to a shortlist, and records why every molecule that left did so.  This
package exposes that as 18 MCP tools, two resources and one prompt.

The tools are grouped by the question they answer rather than by the module they
call, because that is the division an agent reasons in:

``environment`` -- can this machine do it?  Probe the 48 declared backends
without installing anything, measure the CPU, memory, disk and accelerators,
list the stage plugins with their contract signatures and determinism, report
which vendored weights are present, and print the citations a result would owe.

``configuration`` -- is this configuration valid, and what would it do?  Parse
and compile strictly, report the funnel that would actually be built, inspect a
model bundle for the digest that pins it, and emit the offline HTML builder.

``screening`` -- run it, and how did it go?  Plan a screen without executing it,
execute one, and read the durable state and audit log of any run.

``results`` -- give me the product.  The verified shortlist, one CSV per stage
with the evidence columns, and a self-contained report carrying no molecule rows.

``audit`` -- why did it decide that?  The reason codes in aggregate, one
molecule's path through the run, the stereocentres 3D embedding chose, and the
recall of a known panel through the whole funnel.

Two properties of this surface are worth stating because they are not the
defaults for a server of this kind.

**The two expensive tools are asynchronous.**  ``screen`` does its work in a
worker thread, so a run that takes an hour does not take the session with it: the
event loop stays free, the client can still be talked to, and a timed-out call
can be recovered.  A synchronous tool function would be invoked inline on the
event loop and would freeze the server for the duration.

**A retry is safe, and that comes from the runner rather than from this
adapter.**  Artifacts are content-addressed and immutable, stage checkpoints are
keyed by cache key rather than by run id, and resuming re-verifies every
committed checkpoint's bytes before re-using it.  So the correct response to a
call that timed out is to call again with the same ``run_id`` and
``resume=True``.  Very little of a scientific pipeline has that property; here it
is load-bearing and can be relied on.

Nothing in this package fetches an asset, installs a package or reaches the
network.  A configuration naming an unprovisioned asset fails on the way in with
the command to run, rather than acquiring a hundred megabytes because an agent
asked a question.

Usage::

    # Browser debug UI
    mcp dev src/molcascade/mcp_server.py

    # Claude Code
    claude mcp add --transport stdio molcascade -- \\
        python -m molcascade.mcp_server
"""

from __future__ import annotations

from molcascade.mcp._common import require_mcp

FastMCP = require_mcp()

#: The shared server instance.  Submodules register onto this rather than
#: creating their own, so the tool namespace is flat and one process serves
#: everything.
mcp = FastMCP("molcascade")

from molcascade.mcp import (  # noqa: E402 - registration must follow construction
    audit,
    configuration,
    environment,
    resources,
    results,
    screening,
)

environment.register(mcp)
configuration.register(mcp)
screening.register(mcp)
results.register(mcp)
audit.register(mcp)
resources.register(mcp)

__all__ = ["mcp"]
