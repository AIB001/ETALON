"""Read-only host discovery; does not create another planner or scientific journal."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from etalon.mcp._common import Cost, ok, tool


def register(mcp: Any) -> None:
    def etalon_research_hook(event: dict) -> Any:
        """FREE. Native lifecycle adapter; prepares research context and delivery receipts.

        Uses the server's installed ETALON_RESEARCH_HOOK_BINDING, never caller-supplied
        entry or state paths. Writes host delivery metadata only; no scientific write,
        computation, checkpoint creation or RSI. An event is a supplied observation;
        native provenance is verified separately against the host trace.
        """
        import json
        from mcp.types import CallToolResult, TextContent
        from etalon.mcp._common import StdoutToStderr
        from etalon.research_hooks import handle_bound_event
        try:
            with StdoutToStderr():
                result = handle_bound_event(event)
            failed = not result["ok"]
            output = result["host_output"] if not failed else {
                "ok": False, "error": result["receipt"]["error"],
                "receipt_path": result["receipt_path"], "automatic_retry": False}
        except (ValueError, KeyError, OSError) as exc:
            failed = True
            output = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)},
                      "automatic_retry": False}
        # Native hook consumers parse the text as the hook output itself. The
        # ordinary ETALON ok envelope would hide hookSpecificOutput. Protocol
        # isError, unlike an ok:false text alone, is recognized by both hosts.
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(
            output, ensure_ascii=False, separators=(",", ":")))], isError=failed)

    etalon_research_hook.etalon_cost = Cost.FREE
    mcp.tool()(etalon_research_hook)

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_capabilities(detail: str = "full", skills: list[str] | None = None,
                            tools: list[str] | None = None,
                            operations: list[str] | None = None) -> str:
        """FREE. Discover all or selected capabilities; known tools need no discovery call.

        Defaults retain the full catalog. detail='summary' omits file manifests.
        Each selector is exact names: null means all, [] means none. For example,
        skills=['campaign'], tools=[], operations=[] reads just that skill bundle.
        Summary includes entrypoints and complete selected-bundle version digests;
        detail='full' expands their member inventory. This is not a method whitelist.
        """
        from etalon.skills import _selection, catalog

        report = catalog(detail=detail, skills=skills)
        if tools == []:
            tool_report = {}
        elif detail == "full":
            from etalon.mcp.server import costs
            available = costs()
            tool_report = {name: available[name] for name in _selection(tools, available, "tools")}
        else:
            from etalon.mcp.server import tool_catalog
            available = tool_catalog()
            tool_report = {name: available[name] for name in _selection(tools, available, "tools")}
        if operations == []:
            operation_report = {}
        else:
            from etalon.runtime.operations import describe
            available_operations = describe()
            operation_report = {name: available_operations[name]
                                for name in _selection(operations, available_operations, "operations")}
        if detail != "full" or any(value is not None for value in (skills, tools, operations)):
            report.update(detail=detail, scope={"skills": [row["slug"] for row in report["skills"]],
                                               "tools": list(tool_report), "operations": list(operation_report)})
        return ok(**report, tools=tool_report, operations=operation_report,
                  runtime={"python": sys.executable, "module": str(Path(__file__).resolve()),
                           "durable_workers": "Linux /proc and POSIX locks required"})

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_skill_read(slug: str, member: str = "SKILL.md", bundle_ref: dict | None = None) -> str:
        """FREE. Read one text member of an installed skill, with its file and bundle digests.

        Known slugs/members can be read directly; discovery is optional. References are loaded
        only when relevant; no experiment is dispatched or skill use inferred.
        """
        from etalon.skills import read_member

        if bundle_ref is not None and not bundle_ref.get("manifest_sha256"):
            raise ValueError("MCP explicit bundle reads require an expected manifest_sha256")
        return ok(**read_member(slug, member, bundle_ref=bundle_ref))
