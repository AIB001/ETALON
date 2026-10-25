"""Synthetic navigation growth cases; no scientific execution or benefit claim."""
import asyncio
import base64
import json

import pytest

from etalon.active.schema import canonical, digest
from etalon.research.evidence import StaleCursorError, read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.graph_memory import node_identity
from etalon.research.research_context import build_research_context

from test_research_graph_memory import graph_case


def _grow(c):
    original = read_evidence(c["workspace"], c["claim"], view="artifact", limit=32768)
    claim = json.loads(base64.b64decode(original["fragment"]["content"]))
    claims = [c["save"]({**claim, "statement": f"Synthetic conditional conclusion {i}; not a science result"})
              for i in range(20)]
    branches = [{"branch_id": f"branch-{i:02d}", "state": "waiting" if i % 2 else "resolved",
        "basis_refs": [claims[(2 * i) % 20], claims[(2 * i + 1) % 20]],
        "next_read_refs": [c["counter"]], "reconsider_when": "New independent direction-specific evidence. " * 2}
        for i in range(12)]
    checkpoint = {**c["checkpoint_content"], "claim_refs": claims, "branches": branches}
    assert len(canonical(checkpoint).encode()) <= 16384
    ref = c["save"](checkpoint)
    c["publish"]()
    c["entry"].write_text(json.dumps({"workspace": "workspace.json", "checkpoint_ref": ref}))
    return ref, checkpoint


def _all_pages(entry, section, *, cap=4096, limit=2):
    cursor, rows, fragments, stamp = None, [], {}, None
    for _ in range(250):
        page = build_research_context(entry, view="manifest", section=section,
            cursor=cursor, limit=limit, max_bytes=cap)
        assert len(canonical(page).encode()) <= cap
        assert page["execution_permission"] == "not_granted" and not page["automatic_retry"]
        assert page["coverage"]["original_bodies_read_by_host"] == "not_established"
        if stamp is None:
            stamp = page["manifest_digest"]
        assert page["manifest_digest"] == stamp
        for item in page["items"]:
            if "value" in item:
                rows.append(item["value"])
            else:
                fragment = item["fragment"]
                raw = fragments.setdefault(item["index"], bytearray())
                assert len(raw) == fragment["offset"]
                raw.extend(base64.b64decode(fragment["content"]))
                if fragment["next_offset"] is None:
                    rows.append(json.loads(raw))
        cursor = page["next_cursor"]
        if cursor is None:
            assert len(rows) == page["total_count"]
            return rows, stamp
    raise AssertionError("manifest pagination did not terminate")


def test_growth_summary_falls_back_at_default_cap_and_keeps_controls(graph_case):
    c = graph_case
    ref, checkpoint = _grow(c)
    before = file_identity(c["store"].path)
    result = build_research_context(c["entry"], view="summary")
    assert result["view"] == "summary" and result["continuation_card"]["fallback_reason"]
    assert result["checkpoint_ref"] == ref
    assert len(canonical(result).encode()) <= 16384
    assert result["continuation"]["authority_state"] == "paused"
    assert result["continuation"]["pending"] == []
    assert [(b["branch_id"], b["state"]) for b in result["branches"]] == [
        (b["branch_id"], b["state"]) for b in checkpoint["branches"]]
    assert result["watermark"]["digest"] and result["counterevidence"]["endpoint_count"]
    assert result["counterevidence"]["reference_coverage_complete"]
    assert not result["counterevidence"]["coverage_complete"]
    assert not result["counterevidence"]["excerpt_coverage_complete"]
    assert result["counterevidence"]["original_bodies_read_by_host"] == "not_established"
    assert result["resume_frontier"]["read_is_new_progress"] is False
    assert result["items"][0]["ref"] == c["decision"]
    assert file_identity(c["store"].path) == before and not c["store"].actions()
    assert result == build_research_context(c["entry"], view="summary")
    # Existing immutable checkpoints remain independently readable.
    old = build_research_context({"workspace": c["workspace"], "checkpoint_ref": c["checkpoint"]}, view="card")
    assert old["checkpoint_ref"] == c["checkpoint"] and old["continuation"]["authority_state"] == "paused"


def test_manifest_losslessly_pages_branches_refs_and_counter_directions(graph_case):
    c = graph_case
    _, checkpoint = _grow(c)
    card = build_research_context(c["entry"], view="card")
    branches, stamp = _all_pages(card["read_context"], "branches")
    references, ref_stamp = _all_pages(card["read_context"], "references")
    counters, counter_stamp = _all_pages(card["read_context"], "counters")
    assert stamp == ref_stamp == counter_stamp == card["manifest"]["digest"]
    assert branches == checkpoint["branches"]
    index = {item["node_id"]: item["ref"] for item in references}
    assert all(ref == index[node_identity(ref)] for ref in checkpoint["claim_refs"] + checkpoint["counter_refs"])
    assert len(index) == len(references)
    assert any(e["kind"] == "refutes" and e["from_ref"] == c["counter"]
               and e["to_ref"] in checkpoint["claim_refs"] for e in counters)
    for row in references:
        read = read_evidence(read_context=card["read_context"], ref=row["ref"], view="record", limit=1)
        assert read["availability"] == "available"


def test_large_single_manifest_row_uses_exact_fragments(graph_case):
    c = graph_case
    reason = "完整条件只读 " * 200
    checkpoint = {**c["checkpoint_content"], "branches": [{**c["checkpoint_content"]["branches"][0],
        "reconsider_when": reason}]}
    ref = c["save"](checkpoint)
    c["publish"]()
    rows, _ = _all_pages({"workspace": c["workspace"], "checkpoint_ref": ref}, "branches")
    assert rows == checkpoint["branches"]


@pytest.mark.parametrize("view", ["summary", "card", "manifest"])
def test_small_transport_cannot_bypass_corrupt_counter_original(graph_case, view):
    c = graph_case
    _grow(c)
    from pathlib import Path
    Path(c["journal"].get(c["counter"]["record_id"])["path"]).write_text("tampered")
    with pytest.raises(EvidenceError, match="(identity|hash|unavailable)"):
        build_research_context(c["entry"], view=view)


def test_manifest_cursor_is_pinned_and_reports_source_change(graph_case):
    c = graph_case
    card = build_research_context(c["entry"], view="card")
    page = build_research_context(card["read_context"], view="manifest", limit=1)
    assert page["next_cursor"]
    with pytest.raises(StaleCursorError, match="stale"):
        build_research_context(card["read_context"], view="manifest", section="branches", cursor=page["next_cursor"])
    c["save"]({**c["step"], "reason": "new synthetic metadata"})
    c["publish"]()
    with pytest.raises(EvidenceError, match="stale_read_context"):
        build_research_context(card["read_context"], view="manifest", cursor=page["next_cursor"])
    with pytest.raises(StaleCursorError, match="stale"):
        build_research_context(c["entry"], view="manifest", cursor=page["next_cursor"])


def test_card_keeps_graph_counter_gap_distinct_from_reference_inventory(graph_case, monkeypatch):
    import etalon.research.research_context as module
    from etalon.research.graph_memory import build_research_graph
    c = graph_case
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"])
    graph["truncated"] = True
    graph["counter_context_complete"] = False
    monkeypatch.setattr(module, "build_research_graph", lambda *a, **k: graph)
    result = build_research_context(c["entry"], view="card")
    assert result["selection"]["graph_truncated"]
    assert not result["counterevidence"]["graph_counter_context_complete"]
    assert result["counterevidence"]["reference_coverage_complete"]
    assert not result["counterevidence"]["coverage_complete"]


def test_legacy_card_exposes_each_branch_authored_conclusion_without_claims(graph_case):
    c = graph_case
    other = c["save"]({**c["step"], "branch_id": "other", "reason": "OTHER_BRANCH: defer transfer; fixture only",
        "detail": {**c["step"]["detail"], "choice": "defer"}})
    checkpoint = {**c["checkpoint_content"], "claim_refs": [], "step_refs": [c["decision"], other],
        "branches": [*c["checkpoint_content"]["branches"], {**c["checkpoint_content"]["branches"][0],
            "branch_id": "other", "state": "waiting"}]}
    ref = c["save"](checkpoint)
    c["publish"]()
    card = build_research_context({"workspace": c["workspace"], "checkpoint_ref": ref}, view="card")
    latest = {b["branch_id"]: b["latest_step"] for b in card["branches"]}
    assert latest["fixed-core"]["node_id"] == node_identity(c["decision"])
    assert latest["other"]["node_id"] == node_identity(other)
    assert latest["other"]["choice"] == "defer"
    assert "OTHER_BRANCH" in latest["other"]["summary"]["text"]
    assert card["checkpoint_progress"]["order_semantics"] == "checkpoint_list_order_not_execution_order"


def test_mcp_card_and_manifest_use_existing_tool(graph_case):
    from mcp.server.fastmcp import FastMCP
    from etalon.mcp.evidence import register
    server = FastMCP("continuation-card-fixture")
    register(server)
    schemas = {item.name: item.inputSchema for item in asyncio.run(server.list_tools())}
    assert {"card", "manifest"} <= set(schemas["etalon_research_context"]["properties"]["view"]["enum"])
    def call(args):
        result = asyncio.run(server.call_tool("etalon_research_context", args))
        blocks = result[0] if isinstance(result, tuple) else result
        return json.loads(next(block.text for block in blocks if hasattr(block, "text")))
    card = call({"entry": str(graph_case["entry"]), "view": "card"})
    assert card["ok"] and card["continuation"]["authority_state"] == "paused"
    page = call({"entry": card["read_context"], "view": "manifest", "limit": 1,
        "view_options": {"section": "counters"}})
    assert page["ok"] and page["section"] == "counters" and page["items"]


def test_large_counter_body_remains_exact_paged_evidence_not_index_coverage(graph_case):
    from etalon.research import Artifact
    from etalon.research.evidence_refs import make_ref
    c = graph_case
    body = ("SYNTHETIC_COUNTER_SCOPE_ONLY\n" * 2400).encode()
    path = c["entry"].parent / "large-counter.txt"
    path.write_bytes(body)
    artifact = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic", bindings={})
    c["journal"].record(artifact)
    counter = make_ref("science", artifact.id, c["journal"].get(artifact.id))
    cp = c["save"]({**c["checkpoint_content"], "counter_refs": [counter]})
    c["publish"]()
    card = build_research_context({"workspace": c["workspace"], "checkpoint_ref": cp}, view="card")
    counters, _ = _all_pages(card["read_context"], "counters")
    assert {"kind": "checkpoint_counter", "ref": counter} in counters
    assert len(canonical(card).encode()) <= 16384
    assert "base64" not in canonical(card)
    assert card["counterevidence"]["original_bodies_read_by_host"] == "not_established"
    offset, delivered = 0, bytearray()
    while offset is not None:
        read = read_evidence(read_context=card["read_context"], ref=counter,
            view="artifact", offset=offset, limit=32768)
        assert read["verification"]["artifact"] == "verified"
        delivered.extend(base64.b64decode(read["fragment"]["content"]))
        offset = read["fragment"]["next_offset"]
    assert bytes(delivered) == body


@pytest.mark.parametrize("view", ["manifest", "card", "summary"])
def test_reverse_counter_member_checks_member_bytes_not_only_parent_metadata(graph_case, view):
    from etalon.research.evidence_refs import make_ref
    c = graph_case
    member = c["entry"].parent / "raw.bin"
    member.write_bytes(b"member-original")
    identity = file_identity(member)
    parent = {"members": [{"member": "raw.bin", "path": str(member), **identity, "visibility": "released"}]}
    member_ref = {**make_ref("extra", "snapshot-owner", parent), "member": "raw.bin", **identity}
    reverse = {"schema": "etalon-research-claim/1", "question": "Which route?",
        "statement": "Synthetic reverse claim with an exact snapshot-member counter",
        "epistemic_status": "interpretation", "subjects": [], "conditions": [], "limitations": [],
        "support_refs": [], "counter_refs": [c["claim"], member_ref], "method_refs": [], "supersedes": []}
    manifest = c["entry"].parent / "external-manifest.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [
        {"record_id": "snapshot-owner", "record_type": "data_snapshot", "record": parent, "visibility": "released"},
        {"record_id": "reverse-counter", "record_type": "claim", "record": reverse, "visibility": "released"}]}))
    ws = json.loads(c["workspace"].read_text())
    ws["sources"].append({"source_id": "extra", "kind": "artifact_manifest", "location": str(manifest),
        "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}})
    c["workspace"].write_text(json.dumps(ws))
    # The original is valid before corruption and its exact member identity is
    # part of the explicit reverse relation inventory, independent of graph cap.
    valid = build_research_context(c["entry"], view="manifest", section="counters")
    assert any(item.get("value", {}).get("from_ref") == member_ref for item in valid["items"])
    read_context = valid["read_context"]
    member.write_bytes(b"X" + member.read_bytes()[1:])
    with pytest.raises(EvidenceError, match="(hash|identity|unavailable)"):
        read_evidence(read_context=read_context, ref=member_ref, view="artifact", limit=1)
    with pytest.raises(EvidenceError, match="(hash|identity|unavailable)"):
        build_research_context(read_context, view=view)
