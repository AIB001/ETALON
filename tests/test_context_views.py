"""Bounded navigation over synthetic records, not scientific quality evidence."""
import asyncio
import json

import pytest

from etalon.active.schema import canonical, digest
from etalon.research.evidence import read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.graph_memory import node_identity
from etalon.research.research_context import build_research_context

from test_research_graph_memory import graph_case


@pytest.mark.parametrize("view", ["summary", "node", "full"])
def test_views_preserve_identity_controls_and_explicit_counter_direction(graph_case, view):
    c = graph_case
    before = file_identity(c["store"].path)
    context = build_research_context(c["entry"], view=view, node_id=node_identity(c["decision"]))
    assert context["view"] == view
    assert context["checkpoint_ref"] == c["checkpoint"]
    assert context["continuation"]["authority_state"] == "paused"
    assert context["continuation"]["pending"] == []
    assert context["branches"][0]["state"] == "resolved"
    assert context["branches"][0]["reconsider_when"] == "New constraints"
    assert context["execution_permission"] == "not_granted" and not context["automatic_retry"]
    assert any(e["from"] == node_identity(c["counter"]) and e["to"] == node_identity(c["claim"])
               and e["kind"] == "refutes" for e in context["relations"])
    assert context["context_id"] == "research-context-" + digest({k: v for k, v in context.items() if k != "context_id"})
    assert context["publications"]["incomplete_count"] == 0
    assert file_identity(c["store"].path) == before and not c["store"].actions()
    assert context == build_research_context(c["entry"], view=view, node_id=node_identity(c["decision"]))


def test_summary_expands_exact_refs_and_omits_unrelated_details(graph_case):
    c = graph_case
    summary = build_research_context(c["entry"], view="summary")
    full = build_research_context(c["entry"])
    assert full["view"] == "full"
    assert summary["followup"]["evidence_limit"]["maximum"] == 32768
    ref = next(item["ref"] for item in summary["reference_index"] if item["node_id"] == node_identity(c["evidence"]))
    assert ref == c["evidence"]
    read = read_evidence(c["workspace"], ref, view="artifact", limit=32768)
    assert read["verification"]["artifact"] == "verified"
    assert "PRIVATE_SENTINEL" not in canonical(summary)
    assert "base64" not in canonical(summary)
    assert len(summary["items"]) <= 4


def test_updated_checkpoint_summary_exposes_newly_archived_progress(graph_case):
    c = graph_case
    step = c["save"]({**c["step"], "branch_id": "followup", "reason": "NEW_ARCHIVED_PROGRESS: existing results were reviewed"})
    checkpoint = c["save"]({**c["checkpoint_content"], "step_refs": [c["decision"], step], "supersedes": [c["checkpoint"]]})
    c["publish"]()
    c["entry"].write_text(json.dumps({"workspace": "workspace.json", "checkpoint_ref": checkpoint}))
    result = build_research_context(c["entry"], view="summary")
    assert result["checkpoint_progress"]["step_count"] == 2
    assert result["checkpoint_progress"]["tail_step_refs"] == [step]
    frontier = result["resume_frontier"]
    assert result["checkpoint_ref"] == checkpoint and result["checkpoint_progress"]["step_count"] == 2
    assert frontier["latest_steps"][0]["node_id"] == node_identity(step)
    assert "NEW_ARCHIVED_PROGRESS" in frontier["latest_steps"][0]["summary"]["text"]
    assert frontier["read_is_new_progress"] is False
    assert result["items"][0]["ref"] == step
    assert "NEW_ARCHIVED_PROGRESS" in result["items"][0]["summary"]["text"]
    assert result["items"][0]["selection_reason"] == "checkpoint_tail_step"
    assert result["continuation"]["pending"] == []
    assert not c["store"].actions()


def test_summary_keeps_checkpoint_tail_when_old_step_details_fill_graph_bound(graph_case):
    c = graph_case
    steps = []
    for index in range(22):
        steps.append(c["save"]({**c["step"], "reason": f"ARCHIVED_PROGRESS_{index}",
            "detail": {**c["step"]["detail"], "alternatives": [
                {"branch_id": f"omitted-{branch}", "disposition": "skip", "reason": "x" * 300}
                for branch in range(20)]}}))
    checkpoint = c["save"]({**c["checkpoint_content"], "step_refs": steps,
                            "supersedes": [c["checkpoint"]]})
    c["publish"]()
    c["entry"].write_text(json.dumps({"workspace": "workspace.json", "checkpoint_ref": checkpoint}))
    context = build_research_context(c["entry"], view="summary", max_bytes=32768)
    assert context["selection"]["graph_truncated"]
    assert context["items"][0]["ref"] == steps[-1]
    assert context["items"][0]["summary"]["text"] == "ARCHIVED_PROGRESS_21"
    assert context["checkpoint_progress"]["tail_step_refs"] == [steps[-1]]
    assert context["continuation"]["authority_state"] == "paused"


def test_summary_fails_explicitly_if_tail_was_omitted_from_projection(graph_case, monkeypatch):
    import etalon.research.research_context as module
    from etalon.research.graph_memory import build_research_graph
    c = graph_case
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"])
    graph["nodes"] = [n for n in graph["nodes"] if n["ref"] != c["decision"]]
    graph["truncated"] = True
    monkeypatch.setattr(module, "build_research_graph", lambda *args, **kwargs: graph)
    with pytest.raises(EvidenceError, match="tail step is unavailable"):
        build_research_context(c["entry"], view="summary")


def test_node_view_requires_identity_and_excludes_other_branch(graph_case):
    c = graph_case
    unrelated = c["save"]({**c["step"], "branch_id": "unrelated-branch", "reason": "UNRELATED_BRANCH_SENTINEL",
                           "input_refs": [], "detail": {**c["step"]["detail"], "basis_refs": [c["unrelated"]]}})
    c["publish"]()
    result = build_research_context(c["entry"], view="node", node_id=node_identity(c["decision"]))
    assert result["items"][0]["ref"] == c["decision"]
    assert c["counter"] in result["counterevidence"]["selected_refs"]
    assert unrelated not in [item["ref"] for item in result["items"]]
    assert "UNRELATED_BRANCH_SENTINEL" not in canonical(result)
    assert result["selection"]["excluded_by_view"] >= 1
    with pytest.raises(EvidenceError, match="requires node_id"):
        build_research_context(c["entry"], view="node")
    with pytest.raises(EvidenceError, match="unavailable"):
        build_research_context(c["entry"], view="node", node_id="unknown")


@pytest.mark.parametrize("view", ["summary", "node", "full"])
def test_view_budget_is_respected_or_fails_explicitly(graph_case, view):
    c = graph_case
    try:
        result = build_research_context(c["entry"], view=view, node_id=node_identity(c["decision"]), max_bytes=4096)
    except EvidenceError as exc:
        assert "bound" in str(exc)
    else:
        assert len(canonical(result).encode()) <= 4096
        assert result["continuation"]["authority_state"] == "paused"
        if view != "full":
            assert result["counterevidence"]["relationship_count"] == result["counterevidence"]["selected_relationship_count"]


@pytest.mark.parametrize("view", ["summary", "node", "full"])
def test_purpose_and_identity_checks_apply_to_all_views(graph_case, view):
    c = graph_case
    policy = {"record_rules": [{"source_id": "science", "record_id": c["counter"]["record_id"], "allowed_purposes": ["evaluation"]}]}
    with pytest.raises(EvidenceError, match="checkpoint unavailable"):
        build_research_context(c["entry"], view=view, node_id=node_identity(c["decision"]), policy=policy)
    from pathlib import Path
    Path(c["journal"].get(c["counter"]["record_id"])["path"]).write_text("tampered")
    with pytest.raises(EvidenceError, match="(hash|identity|unavailable)"):
        build_research_context(c["entry"], view=view, node_id=node_identity(c["decision"]))


def test_publication_read_failure_cannot_look_like_empty_pending(graph_case):
    c = graph_case
    path = c["entry"].with_name(c["entry"].name + ".publications.json")
    path.write_text("corrupt publication index")
    context = build_research_context(c["entry"], view="summary")
    assert context["continuation"]["pending"] == []
    assert context["publications"]["status"] == "unknown"
    assert context["publications"]["incomplete_count"] is None
    assert not context["automatic_retry"]


def test_dictionary_entry_cannot_claim_it_checked_publication_index(graph_case):
    c = graph_case
    context = build_research_context({"workspace": c["workspace"], "checkpoint_ref": c["checkpoint"]}, view="summary")
    assert context["publications"]["status"] == "not_inspected"
    assert context["publications"]["incomplete_count"] is None


@pytest.mark.parametrize("view", ["summary", "node", "full"])
def test_views_keep_experience_conditions_and_reject_assumed_transfer(graph_case, monkeypatch, view):
    # This test injects an explicitly synthetic experience projection, testing
    # delivery selection only. It does not manufacture a validated RSI record.
    import copy
    import etalon.research.research_context as module
    from etalon.research.graph_memory import build_research_graph
    c = graph_case
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"])
    node = next(n for n in graph["nodes"] if n["ref"] == c["decision"])
    node["experience"] = {"conditions": {"chemical_state": "neutral"}, "invalid_when": "charged state",
                          "outcome": "retain", "lesson": "fixture only", "support_refs": [c["evidence"]],
                          "counter_refs": [c["counter"]], "scope": "fixture"}
    monkeypatch.setattr(module, "build_research_graph", lambda *args, **kwargs: copy.deepcopy(graph))
    kwargs = {"view": view, "node_id": node["node_id"]}
    for condition, expected in ((None, "not_checked_for_new_branch"),
                                ({"chemical_state": "neutral"}, "conditions_match_host_review_required"),
                                ({"chemical_state": "charged"}, "conditions_mismatch")):
        context = build_research_context(c["entry"], conditions=condition, **kwargs)
        experience = next(n["experience"] for n in context["items"] if "experience" in n)
        assert experience["applicability"] == expected
        assert experience["conditions"] == {"chemical_state": "neutral"}
        assert experience["invalid_when"] == "charged state"


def test_mcp_declares_bounds_and_rejects_oversized_read():
    from mcp.server.fastmcp import FastMCP
    from etalon.mcp.evidence import register
    server = FastMCP("context-contract-fixture")
    register(server)
    listed = {item.name: item.inputSchema for item in asyncio.run(server.list_tools())}
    context = listed["etalon_research_context"]["properties"]
    assert context["view"]["enum"] == ["summary", "node", "full", "resources", "branch", "card", "manifest", "reading"]
    assert context["view"]["default"] == "full"
    assert (context["max_bytes"]["minimum"], context["max_bytes"]["maximum"]) == (4096, 65536)
    evidence = listed["etalon_evidence_read"]["properties"]
    assert (evidence["limit"]["minimum"], evidence["limit"]["maximum"]) == (1, 32768)
    assert listed["etalon_research_graph"]["properties"]["max_nodes"]["maximum"] == 128
    with pytest.raises(Exception, match="32768"):
        asyncio.run(server.call_tool("etalon_evidence_read", {"workspace": "F:/missing-workspace.json", "ref": {}, "limit": 40000}))


@pytest.mark.parametrize("value", [0, 4095, 65537, True])
def test_sdk_still_rejects_invalid_byte_bounds(graph_case, value):
    with pytest.raises(EvidenceError, match="4096..65536"):
        build_research_context(graph_case["entry"], max_bytes=value)
