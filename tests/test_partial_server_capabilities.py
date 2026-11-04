"""A composed FastMCP server must advertise its actual callable tool set."""
import asyncio
import json

from mcp.server.fastmcp import FastMCP

from etalon.mcp import host, methods
from etalon.mcp._common import Cost, tool
from etalon.mcp.server import costs, register_tools


def call(server, **arguments):
    raw = asyncio.run(server.call_tool("etalon_capabilities", arguments))
    blocks = raw[0] if isinstance(raw, tuple) else raw
    return json.loads(blocks[0].text)


def test_partial_server_lists_actual_custom_and_native_tools_without_global_registration(monkeypatch):
    server = FastMCP("partial")
    methods.register(server)
    host.register(server)

    @server.tool()
    def common_science(request: str) -> str:
        """Shared task-specific science service."""
        return request

    import etalon.mcp.server as standard
    monkeypatch.setattr(standard, "tool_catalog", lambda: (_ for _ in ()).throw(AssertionError("global registration forbidden")))
    monkeypatch.setattr(standard, "costs", lambda: (_ for _ in ()).throw(AssertionError("global registration forbidden")))
    expected = {item.name for item in asyncio.run(server.list_tools())}
    for detail in ("full", "summary"):
        result = call(server, detail=detail, skills=[], operations=[])
        assert result["ok"] and set(result["tools"]) == expected
        assert "etalon_resource_feedback" in result["tools"]
        assert "etalon_action_submit" not in result["tools"]
        custom = result["tools"]["common_science"]
        assert (custom if detail == "full" else custom["cost"]) == "not_declared"
    invalid = call(server, skills=[], operations=[], tools=["etalon_action_submit"])
    assert invalid["ok"] is False

    @server.tool(name="later_alias", description="An explicitly registered later tool.")
    @tool(Cost.CHEAP)
    def later_function() -> str:
        return "{}"

    later = call(server, detail="summary", skills=[], operations=[], tools=["later_alias"])
    assert later["tools"]["later_alias"] == {"cost": "cheap", "description": "An explicitly registered later tool."}


def test_full_standard_fastmcp_preserves_cost_catalog_and_runtime_operation_scope():
    server = FastMCP("standard")
    register_tools(server)
    result = call(server, skills=[])
    assert result["ok"] and result["tools"] == costs()
    assert set(result["tools"]) == {item.name for item in asyncio.run(server.list_tools())}
    assert "method.run" in result["operations"]

    partial = FastMCP("host-only")
    host.register(partial)
    local = call(partial, skills=[])
    assert "method.run" in local["operations"]
    assert "etalon_method_call" not in local["tools"]


def test_registration_only_collector_keeps_legacy_fallback():
    class Collector:
        def __init__(self):
            self.tools = {}
        def tool(self):
            def add(function):
                self.tools[function.__name__] = function
                return function
            return add

    collector = Collector()
    host.register(collector)
    result = json.loads(collector.tools["etalon_capabilities"](skills=[], operations=[]))
    assert result["ok"] and result["tools"] == costs()
