"""Explicit scoped navigation over fixtures; no new scientific observations."""
import json
from pathlib import Path

import pytest

from etalon.active.schema import canonical, digest
from etalon.research import Artifact
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.graph_memory import build_research_graph, node_identity
from etalon.research.research_context import build_research_context

from test_research_graph_memory import graph_case


def _raw(c, name):
    path = c["entry"].parent / name
    path.write_text("Synthetic counter fixture: " + name, encoding="utf-8")
    item = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic", bindings={})
    c["journal"].record(item)
    return make_ref("science", item.id, c["journal"].get(item.id))


def _claim(c, counters):
    return c["save"]({"schema": "etalon-research-claim/1", "question": "Which route?",
        "statement": "Synthetic scoped claim", "epistemic_status": "interpretation",
        "subjects": ["fixture"], "conditions": ["fixture only"], "limitations": ["No science"],
        "support_refs": [c["evidence"]], "counter_refs": counters, "method_refs": [], "supersedes": []})


@pytest.mark.parametrize("view", ["summary", "node", "full"])
def test_explicit_scope_preserves_controls_pointers_and_repeatable_receipt(graph_case, view):
    c = graph_case
    before = file_identity(c["store"].path)
    scope = {"root_refs": [c["decision"]], "ordinary_hops": 1}
    result = build_research_context(c["entry"], scope=scope, view=view, max_bytes=32768)
    assert result["scope"] == scope
    assert result["scope_receipt"]["reference_coverage_complete"]
    assert result["scope_receipt"]["counter_reference_coverage_complete"]
    assert result["counterevidence"]["reference_coverage_complete"]
    assert result["scope_receipt"]["global_reference_coverage_complete"] is False
    assert result["selection"]["coverage_complete"] is False
    assert result["counterevidence"]["coverage_complete"] is False
    assert result["continuation"]["authority_state"] == "paused"
    assert result["continuation"]["pending"] == []
    assert result["checkpoint_progress"]["tail_step_refs"] == [c["decision"]]
    assert result["task_workset"] and result["source_watermarks"]
    assert result["execution_permission"] == "not_granted" and not result["automatic_retry"]
    assert result["context_id"] == "research-context-" + digest({k: v for k, v in result.items() if k != "context_id"})
    assert len(canonical(result).encode()) <= 32768
    repeated = build_research_context(result["read_context"], scope=result["scope"], view=view, max_bytes=32768)
    assert repeated["scope_receipt"] == result["scope_receipt"]
    assert repeated["reference_index"] == result["reference_index"]
    assert file_identity(c["store"].path) == before and not c["store"].actions()


def test_summary_excerpt_cap_does_not_drop_counter_references(graph_case):
    c = graph_case
    counters = [_raw(c, f"counter-{index}.txt") for index in range(6)]
    claim = _claim(c, counters)
    c["publish"]()
    result = build_research_context(c["entry"], view="summary",
        scope={"root_refs": [claim], "ordinary_hops": 0}, max_bytes=32768)
    assert len(result["items"]) <= 4
    refs = [item["ref"] for item in result["reference_index"]]
    assert all(ref in refs for ref in counters + [claim])
    assert result["scope_receipt"]["counter_reference_coverage_complete"]
    assert result["counterevidence"]["reference_coverage_complete"]
    assert not result["counterevidence"]["excerpt_coverage_complete"]
    assert not result["excerpt_coverage"]["complete"]
    assert result["excerpt_coverage"]["omitted_counter_endpoint_count"] > 0
    assert result["selection"]["item_limit"] == 4
    directions = {(row["from"], row["to"]) for row in result["relations"] if row["kind"] == "refutes"}
    assert all((node_identity(ref), node_identity(claim)) in directions for ref in counters)
    assert "omitted_excerpt_refs" in result["followup"]


def test_explicit_root_not_rejected_by_prior_broad_graph_truncation(graph_case):
    c = graph_case
    candidates = []
    for index in range(140):
        candidates.append(c["save"]({**c["step"], "branch_id": f"other-{index}",
            "reason": f"EXACT_ROOT_AFTER_BROAD_BOUND_{index}"}))
    c["publish"]()
    broad = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], max_nodes=128, max_bytes=131072)
    assert broad["truncated"]
    broad_nodes = {node["node_id"] for node in broad["nodes"]}
    target = next(ref for ref in candidates if node_identity(ref) not in broad_nodes)
    result = build_research_context(c["entry"], scope={"root_refs": [target], "ordinary_hops": 0},
        view="node", node_id=node_identity(target), max_bytes=32768)
    assert result["items"][0]["ref"] == target
    assert "EXACT_ROOT_AFTER_BROAD_BOUND" in result["items"][0]["summary"]["text"]
    assert result["scope_receipt"]["reference_coverage_complete"]
    assert not result["scope_receipt"]["global_reference_coverage_complete"]


def test_scoped_source_gap_never_becomes_complete_counter_search(graph_case):
    c = graph_case
    config = json.loads(c["workspace"].read_text())
    config["sources"].append({"source_id": "missing", "kind": "artifact_manifest",
        "location": str(c["entry"].parent / "missing.json"),
        "identity": {"kind": "snapshot_sha256", "value": "0" * 64}})
    c["workspace"].write_text(json.dumps(config))
    result = build_research_context(c["entry"], scope={"root_refs": [c["decision"]]},
        view="summary", max_bytes=32768)
    assert not result["scope_receipt"]["reference_coverage_complete"]
    assert not result["scope_receipt"]["counter_reference_coverage_complete"]
    assert not result["scope_receipt"]["counter_search_complete"]
    assert not result["counterevidence"]["reference_coverage_complete"]
    assert any(source["status"] != "complete" for source in result["source_watermarks"])


def test_scope_boundaries_offer_exact_followup_without_widening_permission(graph_case):
    c = graph_case
    result = build_research_context(c["entry"], scope={"root_refs": [c["decision"]], "ordinary_hops": 0},
        view="summary", max_bytes=32768)
    assert node_identity(c["evidence"]) in result["scope_receipt"]["deliberate_boundary_node_ids"]
    assert c["evidence"] in [row["ref"] for row in result["reference_index"]]
    assert result["followup"]["evidence_scope"] == "pass read_context; do not infer workspace paths"
    assert "boundary_ref_source" in result["followup"]
    policy = {"record_rules": [{"source_id": "science", "record_id": c["counter"]["record_id"],
        "allowed_purposes": ["evaluation"]}]}
    with pytest.raises(EvidenceError, match="(checkpoint unavailable|scope|restricted)"):
        build_research_context(c["entry"], scope=result["scope"], policy=policy, view="summary")


def test_scope_still_rejects_changed_original_bytes(graph_case):
    c = graph_case
    Path(c["journal"].get(c["counter"]["record_id"])["path"]).write_text("tampered")
    with pytest.raises(EvidenceError, match="(identity|hash|unavailable)"):
        build_research_context(c["entry"], scope={"root_refs": [c["claim"]]}, view="summary")


def test_compact_receipt_retains_every_boundary_and_counter_direction(graph_case):
    c = graph_case
    scope = {"root_refs": [c["decision"]], "ordinary_hops": 0}
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], scope=scope)
    context = build_research_context(c["entry"], scope=scope, view="summary", max_bytes=32768)
    receipt = context["scope_receipt"]
    refs = {row["node_id"]: row["ref"] for row in context["reference_index"]}
    assert receipt["schema"] == "etalon-research-context-scope/1"
    assert {(group["from"], target, group["relation"])
        for group in receipt["boundary_relation_groups"] for target in group["to_node_ids"]} == {
        (edge["from"], edge["to"], edge["relation"]) for edge in graph["scope_receipt"]["boundary_relations"]}
    expanded_counters = [{"from_ref": refs[edge["from"]], "to_ref": refs[edge["to"]], "kind": edge["kind"],
        "basis_ref": refs[edge["basis_node_id"]]} for edge in context["relations"] if edge["kind"] == "refutes"]
    assert sorted(map(canonical, expanded_counters)) == sorted(map(canonical, graph["scope_receipt"]["counter_relations"]))
    for kind in ("root", "control", "counter_endpoint", "deliberate_boundary"):
        assert [refs[nid] for nid in receipt[kind + "_node_ids"]] == graph["scope_receipt"][kind + "_refs"]
