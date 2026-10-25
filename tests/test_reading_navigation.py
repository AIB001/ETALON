"""Bounded optional navigation fixtures; no scientific execution or benefit claim."""
import base64
import json

import pytest

from etalon.active.schema import canonical
from etalon.research.evidence import StaleCursorError, read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.graph_memory import node_identity
from etalon.research.research_context import build_research_context

from test_research_graph_memory import graph_case
from test_mcp import Collector


def test_exact_original_navigation_avoids_inventory_walk_and_preserves_pause(graph_case):
    c = graph_case
    before = file_identity(c["store"].path)
    card = build_research_context(c["entry"], view="card")
    assert len(canonical(card).encode()) <= 14336
    latest = card["branches"][0]["latest_step"]
    assert latest["ref"] == c["decision"]
    assert latest["original_read"] == {"view": "artifact", "limit": 32768}
    assert card["reading_menu"]["question_detail"]["scope"]["ordinary_hops"] == 0
    assert "own read_context" in card["reading_menu"]["cross_workspace"]
    page = build_research_context(card["read_context"], view="manifest", node_id=node_identity(c["decision"]))
    assert page["total_count"] == 1 and page["next_cursor"] is None
    assert page["selection"]["inventory_count"] > 1
    row = page["items"][0]["value"]
    assert row["ref"] == c["decision"] and row["record_type"] == "artifact"
    assert row["content_schema"] == "etalon-research-step/1"
    assert row["original_locator"]["sha256"] == c["journal"].get(c["decision"]["record_id"])["sha256"]
    original = read_evidence(read_context=card["read_context"], ref=row["ref"], **row["original_read"])
    assert json.loads(base64.b64decode(original["fragment"]["content"])) == c["step"]
    assert card["continuation"]["authority_state"] == "paused"
    assert card["counterevidence"]["original_bodies_read_by_host"] == "not_established"
    assert file_identity(c["store"].path) == before and not c["store"].actions()


def test_typed_record_and_member_navigation_never_guesses_artifact_view(graph_case):
    c = graph_case
    member = c["entry"].parent / "released.bin"
    member.write_bytes(b"exact fixture member")
    parent = {"members": [{"member": "released.bin", "path": str(member),
                           **file_identity(member), "visibility": "released"}]}
    evaluation = {"status": "failed", "report_ref": c["counter"]}
    refs = [make_ref("extra", "evaluation", evaluation), make_ref("extra", "snapshot", parent)]
    refs.append({**refs[1], "member": "released.bin", **file_identity(member)})
    cp = c["save"]({**c["checkpoint_content"], "branches": [{**c["checkpoint_content"]["branches"][0],
        "next_read_refs": refs}]})
    c["publish"]()
    manifest = c["entry"].parent / "external.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [
        {"record_id": "evaluation", "record_type": "method_evaluation", "record": evaluation, "visibility": "released"},
        {"record_id": "snapshot", "record_type": "data_snapshot", "record": parent, "visibility": "released"}]}))
    ws = json.loads(c["workspace"].read_text())
    ws["sources"].append({"source_id": "extra", "kind": "artifact_manifest", "location": str(manifest),
        "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}})
    c["workspace"].write_text(json.dumps(ws))
    entry = {"workspace": c["workspace"], "checkpoint_ref": cp}
    for ref, expected in zip(refs, ("record", "record", "artifact")):
        page = build_research_context(entry, view="manifest", node_id=node_identity(ref), max_bytes=4096)
        assert len(canonical(page).encode()) <= 4096
        row = page["items"][0]["value"]
        assert row["original_read"]["view"] == expected
        if ref == refs[1]:
            assert "explicitly released member" in row["member_read"]
        read = read_evidence(read_context=page["read_context"], ref=row["ref"], **row["original_read"])
        assert read["availability"] == "available" and not read["missing"]
        if expected == "artifact":
            assert base64.b64decode(read["fragment"]["content"]) == member.read_bytes()


def test_exact_selection_is_not_search_or_workspace_expansion(graph_case):
    c = graph_case
    for node in ("missing", node_identity(c["unrelated"]), "fixed-core"):
        with pytest.raises(EvidenceError, match="unavailable"):
            build_research_context(c["entry"], view="manifest", node_id=node)
    with pytest.raises(EvidenceError, match="section references"):
        build_research_context(c["entry"], view="manifest", section="counters", node_id=node_identity(c["counter"]))
    page = build_research_context(c["entry"], view="manifest", limit=1)
    with pytest.raises(StaleCursorError):
        build_research_context(c["entry"], view="manifest", node_id=node_identity(c["decision"]), cursor=page["next_cursor"])
    with pytest.raises(EvidenceError, match="without question"):
        build_research_context(c["entry"], view="manifest", question="choose for me")


def test_mcp_exact_manifest_and_record_guidance(graph_case):
    from etalon.mcp.evidence import register
    collector = Collector()
    register(collector)
    response = json.loads(collector.tools["etalon_research_context"](str(graph_case["entry"]),
        view="manifest", node_id=node_identity(graph_case["counter"])))
    assert response["ok"] and response["total_count"] == 1
    assert response["items"][0]["value"]["original_read"]["view"] == "artifact"


def test_exact_reference_selection_still_checks_unselected_counter_body(graph_case):
    from pathlib import Path
    c = graph_case
    Path(c["journal"].get(c["counter"]["record_id"])["path"]).write_bytes(b"corrupted original")
    with pytest.raises(EvidenceError, match="identity|hash|unavailable"):
        build_research_context(c["entry"], view="manifest", node_id=node_identity(c["decision"]))
