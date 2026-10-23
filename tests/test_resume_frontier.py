"""First-read checkpoint navigation fixtures; not model-consumption evidence."""
import base64
import json

import pytest

from etalon.active.schema import canonical
from etalon.research import Artifact
from etalon.research.evidence import read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.graph_memory import node_identity
from etalon.research.research_context import build_research_context
from test_research_graph_memory import graph_case


def _artifact(c, name, payload):
    path = c["entry"].parent / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    artifact = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic",
                        bindings={"schema": payload["schema"]})
    c["journal"].record(artifact)
    return make_ref("science", artifact.id, artifact.as_dict())


def _advance(c, steps, *, predecessor=None):
    checkpoint = c["save"]({**c["checkpoint_content"], "step_refs": steps,
                            "supersedes": [predecessor or c["checkpoint"]]})
    c["publish"]()
    c["entry"].write_text(json.dumps({"workspace": "workspace.json", "checkpoint_ref": checkpoint}), encoding="utf-8")
    return checkpoint


@pytest.fixture
def archived_audit(graph_case):
    c = graph_case
    steps = [c["decision"]] + [c["save"]({**c["step"], "reason": f"Prior archived review {number}"})
                               for number in range(15)]
    predecessor = _advance(c, steps)
    audit = _artifact(c, "audit-fixture.json", {"schema": "etalon-hook-readback-audit/1",
        "marker": "BYTE_AUDIT_V3_FIXTURE", "scientific_conclusion_changed": False,
        "scientific_runs_submitted": 0, "rsi_triggered": False})
    tail = c["save"]({**c["step"], "reason": "BYTE_AUDIT_V3_FIXTURE: archived original-byte audit for review",
        "output_refs": [audit], "detail": {**c["step"]["detail"], "alternatives": [
            {"branch_id": f"alternative-{number}", "disposition": "skip", "reason": "Scope detail " + "x" * 430}
            for number in range(24)]}})
    checkpoint = _advance(c, [*steps, tail], predecessor=predecessor)
    return c, checkpoint, tail, audit


def test_first_summary_exposes_seventeenth_archived_step_even_when_its_body_is_omitted(archived_audit):
    c, checkpoint, tail, audit = archived_audit
    source = file_identity(c["store"].path)
    events = c["store"].events()
    result = build_research_context(c["entry"], view="summary", max_bytes=16384)
    assert len(canonical(result).encode()) <= 16384
    frontier = result["resume_frontier"]
    assert result["checkpoint_ref"] == checkpoint and result["checkpoint_progress"]["step_count"] == 17
    latest = frontier["latest_steps"][0]
    refs = {row["node_id"]: row["ref"] for row in result["reference_index"]}
    assert refs[latest["node_id"]] == tail and latest["operation"] == "decision"
    assert "BYTE_AUDIT_V3_FIXTURE" in latest["summary"]["text"]
    assert latest["output_refs"] == [audit]
    assert latest["output_ref_count"] == 1 and latest["output_refs_omitted"] == 0
    assert result["selection"]["tail_details_omitted"] > 0
    assert tail not in [item["ref"] for item in result["items"]]
    assert frontier["scientific_conclusion_change"] == "not_established_by_context_read"
    assert frontier["read_is_new_progress"] is False and frontier["followup_refs"] == []
    assert result["continuation"]["authority_state"] == "paused"
    assert result["continuation"]["pending"] == []
    assert result["publications"]["incomplete_count"] == 0
    assert result["execution_permission"] == "not_granted" and not result["automatic_retry"]
    assert result["checkpoint_progress"]["tail_step_refs"] == [tail]
    assert any(row["ref"] == tail for row in result["reference_index"])
    assert any(edge["kind"] == "refutes" and edge["from"] == node_identity(c["counter"])
               and edge["to"] == node_identity(c["claim"]) for edge in result["relations"])
    output = read_evidence(ref=latest["output_refs"][0], read_context=result["read_context"], view="artifact", limit=32768)
    assert output["verification"]["artifact"] == "verified"
    assert json.loads(base64.b64decode(output["fragment"]["content"]))["scientific_runs_submitted"] == 0
    # Re-reading the same checkpoint cannot turn old archival work into progress.
    assert build_research_context(c["entry"], view="summary", max_bytes=16384) == result
    assert file_identity(c["store"].path) == source and c["store"].events() == events
    assert not c["store"].actions()


def test_bounded_output_inventory_gives_exact_conditional_followup(graph_case):
    c = graph_case
    outputs = [_artifact(c, f"output-{number}.json", {"schema": "fixture-opaque-output/1", "number": number})
               for number in range(7)]
    tail = c["save"]({**c["step"], "reason": "Archive seven existing fixture outputs", "output_refs": outputs})
    _advance(c, [c["decision"], tail])
    result = build_research_context(c["entry"], view="summary")
    frontier = result["resume_frontier"]
    latest = frontier["latest_steps"][0]
    assert 0 < len(latest["output_refs"]) <= 4
    assert latest["output_refs"] == outputs[:len(latest["output_refs"])]
    assert latest["output_ref_count"] == 7
    assert latest["output_refs_omitted"] == 7 - len(latest["output_refs"])
    assert frontier["followup_refs"] == [{"ref": tail, "reason": "output_references_omitted",
                                         "when": "if the remaining archived outputs are needed"}]
    full = read_evidence(ref=frontier["followup_refs"][0]["ref"], read_context=result["read_context"],
                         view="artifact", limit=32768)
    assert json.loads(base64.b64decode(full["fragment"]["content"]))["output_refs"] == outputs


def test_frontier_does_not_bypass_denied_latest_output(archived_audit):
    c, _, _, audit = archived_audit
    policy = {"record_rules": [{"source_id": audit["source_id"], "record_id": audit["record_id"],
                                "allowed_purposes": []}]}
    with pytest.raises(EvidenceError):
        build_research_context(c["entry"], view="summary", policy=policy)


def test_many_branch_controls_and_latest_output_fit_a_16k_first_read(graph_case):
    c = graph_case
    audit = _artifact(c, "bounded-audit.json", {"schema": "fixture-opaque-output/1", "status": "verified"})
    tail = c["save"]({**c["step"], "reason": "Latest archived fixture audit; no new execution",
                      "output_refs": [audit]})
    condition = ("Reconsider only after checking the original constraint, the explicit negative observation, "
                 "the unresolved applicability condition and the recorded resource limit. ") * 3
    branches = [{**c["checkpoint_content"]["branches"][0], "branch_id": f"retained-branch-{index}",
                 "reconsider_when": condition[:430]} for index in range(13)]
    checkpoint = c["save"]({**c["checkpoint_content"], "step_refs": [c["decision"], tail],
                            "branches": branches, "supersedes": [c["checkpoint"]]})
    c["publish"]()
    c["entry"].write_text(json.dumps({"workspace": "workspace.json", "checkpoint_ref": checkpoint}), encoding="utf-8")
    generous = build_research_context(c["entry"], view="summary", max_bytes=32768)
    bounded = build_research_context(c["entry"], view="summary", max_bytes=16384)
    assert len(canonical(bounded).encode()) <= 16384
    assert bounded["selection"]["excluded_by_budget"] > 0
    for field in ("checkpoint_ref", "checkpoint_progress", "branches", "relations", "reference_index",
                  "continuation", "publications", "source_watermarks", "source_issues"):
        assert bounded[field] == generous[field]
    assert len(bounded["branches"]) == 13
    assert bounded["continuation"]["authority_state"] == "paused"
    assert bounded["execution_permission"] == "not_granted"
    assert any(edge["kind"] == "refutes" for edge in bounded["relations"])
    latest = bounded["resume_frontier"]["latest_steps"][0]
    refs = {row["node_id"]: row["ref"] for row in bounded["reference_index"]}
    assert refs[latest["node_id"]] == tail
    assert latest["summary"]["text"] == "Latest archived fixture audit; no new execution"
    assert latest["output_refs"] == [audit]
    # Compaction must not turn the exact output identity into a cached assertion.
    path = c["entry"].parent / "bounded-audit.json"
    path.write_bytes(path.read_bytes().replace(b"verified", b"modified"))
    with pytest.raises(EvidenceError):
        build_research_context(c["entry"], view="summary", max_bytes=16384)


def test_frontier_reports_source_and_counter_coverage_gaps(graph_case, monkeypatch):
    # Deterministic projection fault fixture; no unavailable original is invented.
    from etalon.research import research_context as module
    from etalon.research.graph_memory import build_research_graph
    c = graph_case
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"])
    graph["issues"].append({"code": "fixture_incomplete_source", "message": "Explicit test fault"})
    graph["counter_context_complete"] = False
    monkeypatch.setattr(module, "build_research_graph", lambda *args, **kwargs: graph)
    result = build_research_context(c["entry"], view="summary")
    coverage = result["counterevidence"]
    assert not coverage["coverage_complete"]
    assert not coverage["context_complete"]
    assert not result["selection"]["coverage_complete"]
    assert any(issue["code"] == "fixture_incomplete_source" for issue in result["source_issues"])
    assert coverage["relationship_count"] == len(result["relations"])
    assert result["continuation"]["authority_state"] == "paused"


@pytest.mark.parametrize("view", ["node", "summary"])
def test_explicit_node_still_fails_when_frontier_cannot_replace_required_details(archived_audit, view):
    c, _, tail, _ = archived_audit
    with pytest.raises(EvidenceError, match="selected node and control metadata exceed context bound"):
        build_research_context(c["entry"], view=view, node_id=node_identity(tail), max_bytes=16384)


def test_no_output_or_gap_does_not_create_a_forced_followup(graph_case):
    frontier = build_research_context(graph_case["entry"], view="summary")["resume_frontier"]
    assert frontier["latest_steps"][0]["output_refs"] == []
    assert frontier["followup_refs"] == []
    assert frontier["read_is_new_progress"] is False
