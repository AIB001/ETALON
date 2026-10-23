"""Remote-style handoff, snapshot drift and scope counterexamples (fixtures)."""
import asyncio
import json

import pytest

from etalon.research.evidence import query_evidence, read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.research_context import build_research_context
from test_research_graph_memory import graph_case


def test_locator_to_pinned_reads_without_client_workspace(graph_case):
    c = graph_case
    before = file_identity(c["store"].path)
    context = build_research_context({"entry": str(c["entry"])}, view="summary")
    handle = json.loads(json.dumps(context["read_context"]))  # transport boundary
    assert context["entry_status"] == "unchanged"
    assert read_evidence(ref=c["evidence"], read_context=handle)["availability"] == "available"
    assert query_evidence(read_context=handle, limit=1)["items"]
    assert build_research_context(handle, view="node",
        node_id=context["items"][0]["node_id"])["checkpoint_ref"] == c["checkpoint"]
    assert read_evidence(ref=c["evidence"], entry={"entry": str(c["entry"])})["availability"] == "available"
    assert file_identity(c["store"].path) == before and not c["store"].actions()


def test_entry_advance_does_not_switch_pinned_checkpoint(graph_case):
    c = graph_case
    old = build_research_context(c["entry"])["read_context"]
    # Entry advancement itself cannot invalidate an intact prior snapshot.
    entry = json.loads(c["entry"].read_text())
    entry["checkpoint_ref"] = c["decision"]  # deliberately not a valid new checkpoint
    c["entry"].write_text(json.dumps(entry))
    pinned = build_research_context(old, view="summary")
    assert pinned["entry_status"] == "advanced"
    assert pinned["checkpoint_ref"] == c["checkpoint"]
    assert read_evidence(ref=c["evidence"], read_context=old)["availability"] == "available"
    with pytest.raises(EvidenceError, match="checkpoint"):
        build_research_context(c["entry"])


def test_workspace_mutation_and_conflicting_selectors_fail_closed(graph_case):
    c = graph_case
    handle = build_research_context(c["entry"])["read_context"]
    with pytest.raises(EvidenceError, match="exactly one"):
        read_evidence(c["workspace"], c["evidence"], read_context=handle)
    changed = json.loads(c["workspace"].read_text())
    changed["workspace_id"] = "different-publication"
    c["workspace"].write_text(json.dumps(changed))
    with pytest.raises(EvidenceError, match="stale_read_context"):
        read_evidence(ref=c["evidence"], read_context=handle)


def test_policy_survives_followup_and_handle_cannot_be_silently_edited(graph_case):
    c = graph_case
    policy = {"record_rules": [{"source_id": "science", "record_id": c["unrelated"]["record_id"],
                                "allowed_purposes": []}]}
    handle = build_research_context(c["entry"], policy=policy)["read_context"]
    result = read_evidence(ref=c["unrelated"], read_context=handle)
    assert result["availability"] != "available"
    assert c["unrelated"] not in [x["ref"] for x in query_evidence(read_context=handle)["items"]]
    handle["policy"] = {}
    with pytest.raises(EvidenceError, match="digest"):
        query_evidence(read_context=handle)


def test_recursive_locator_is_rejected(graph_case):
    entry = graph_case["entry"]
    entry.write_text(json.dumps({"entry": str(entry)}))
    with pytest.raises(EvidenceError, match="locator_depth"):
        build_research_context(entry)


def test_source_policy_is_applied_once_and_retained_on_pinned_context(graph_case):
    c = graph_case
    manifest = json.loads(c["workspace"].read_text())
    manifest["sources"].append({**manifest["sources"][0], "source_id": "withheld", "location": "do-not-open.sqlite"})
    c["workspace"].write_text(json.dumps(manifest))
    policy = {"source_ids": ["science", "withheld"], "source_purposes": {"withheld": ["evaluation"]}}
    context = build_research_context(c["entry"], policy=policy)
    pinned = build_research_context(context["read_context"])
    assert pinned["selection"]["policy"] == context["selection"]["policy"]
    assert pinned["selection"]["policy"]["excluded_source_count"] == 1
    assert all(x["source_id"] == "science" for x in query_evidence(read_context=context["read_context"])["coverage"])


def test_mcp_locator_and_evidence_read_schema_and_runtime(graph_case):
    from mcp.server.fastmcp import FastMCP
    from etalon.mcp.evidence import register
    server = FastMCP("handoff-fixture")
    register(server)

    async def run():
        async def call(name, args):
            result = await server.call_tool(name, args)
            if isinstance(result, tuple):
                result = result[0]
            return json.loads(result[0].text)
        context = await call("etalon_research_context", {"entry": {"entry": str(graph_case["entry"])}, "view": "summary"})
        # The common MCP envelope is a contract; exercise the real decorated tool.
        data = context.get("data", context)
        return await call("etalon_evidence_read", {"ref": graph_case["evidence"], "read_context": data["read_context"]})
    result = asyncio.run(run())
    assert result.get("data", result)["availability"] == "available"
