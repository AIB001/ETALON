"""Synthetic retrieval contracts; these are not scientific validation runs."""
from pathlib import Path

import pytest

from etalon.active.schema import canonical, digest
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.graph_memory import build_research_graph, node_identity
from test_research_graph_memory import graph_case


def scoped(case, root=None, **kwargs):
    return build_research_graph(case["workspace"], checkpoint_ref=case["checkpoint"],
        scope={"root_refs": [root or case["decision"]], "ordinary_hops": 0},
        max_nodes=128, max_bytes=131072, **kwargs)


def test_exact_roots_boundary_controls_and_deterministic_receipt(graph_case):
    c = graph_case
    before = file_identity(c["store"].path)
    result = scoped(c)
    receipt = result["scope_receipt"]
    refs = [n["ref"] for n in result["nodes"]]
    assert receipt["root_refs"] == [c["decision"]]
    assert c["checkpoint"] in refs and c["decision"] in refs
    assert c["counter"] in receipt["counter_endpoint_refs"]
    assert c["evidence"] in receipt["deliberate_boundary_refs"]
    assert receipt["reference_coverage_complete"] and receipt["counter_reference_coverage_complete"]
    assert receipt["traversal_complete"] and receipt["original_verification_complete"]
    assert not receipt["global_reference_coverage_complete"] and not receipt["excerpt_coverage_complete"]
    assert "opaque_original_bodies" in receipt["reference_semantics"]
    assert "metadata_identity_checks_only" in receipt["original_verification_scope"]
    assert result["continuation"]["authority_state"] == "paused"
    assert result["branches"] == c["checkpoint_content"]["branches"]
    assert result["checkpoint_progress"]["tail_step_refs"] == [c["decision"]]
    assert result == scoped(c)
    assert file_identity(c["store"].path) == before and not c["store"].actions()
    assert result["view_id"] == "research-graph-" + digest({k: v for k, v in result.items() if k != "view_id"})


def test_large_unrelated_history_is_an_explicit_boundary_not_fake_global_completion(graph_case):
    c = graph_case
    old = [c["save"]({**c["step"], "branch_id": "old", "reason": f"old unrelated step {i}"})
           for i in range(140)]
    c["publish"]()
    broad = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], max_nodes=128, max_bytes=131072)
    assert broad["truncated"]
    graph = scoped(c)
    receipt = graph["scope_receipt"]
    assert len(graph["nodes"]) < 128 and len(canonical(graph).encode()) <= 131072
    assert receipt["reference_coverage_complete"] and not receipt["global_reference_coverage_complete"]
    assert receipt["counter_search_records"] >= 140
    assert not any(n["ref"] in old for n in graph["nodes"])
    assert graph["checkpoint_progress"]["step_count"] == 1


def test_hop_one_adds_ordinary_dependency_without_rewriting_global_links(graph_case):
    c = graph_case
    zero = scoped(c)
    one = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
        scope={"root_refs": [c["decision"]], "ordinary_hops": 1}, max_nodes=128, max_bytes=131072)
    assert c["evidence"] in zero["scope_receipt"]["deliberate_boundary_refs"]
    assert c["evidence"] in [n["ref"] for n in one["nodes"]]
    assert one["scope_receipt"]["requested_scope"]["ordinary_hops"] == 1
    assert c["evidence"] not in one["scope_receipt"]["deliberate_boundary_refs"]


def test_counter_incidence_closes_reverse_cross_task_edges_and_preserves_direction(graph_case):
    c = graph_case
    other = c["save"]({"schema": "etalon-research-claim/1", "question": "other task",
        "statement": "Other task counter declaration", "epistemic_status": "hypothesis",
        "subjects": ["other task"], "conditions": [], "limitations": [],
        "support_refs": [], "counter_refs": [c["decision"]], "method_refs": [], "supersedes": []})
    chain = c["save"]({"schema": "etalon-research-claim/1", "question": "third task",
        "statement": "Another reverse counter declaration", "epistemic_status": "hypothesis",
        "subjects": ["third task"], "conditions": [], "limitations": [],
        "support_refs": [], "counter_refs": [other], "method_refs": [], "supersedes": []})
    c["publish"]()
    result = scoped(c)
    relations = result["scope_receipt"]["counter_relations"]
    for source, target in ((c["decision"], other), (other, chain)):
        assert {"from_ref": source, "to_ref": target, "kind": "refutes", "basis_ref": target} in relations
        assert any(e["from"] == node_identity(source) and e["to"] == node_identity(target)
                   and e["kind"] == "refutes" for e in result["edges"])
    assert all(ref not in result["scope_receipt"]["deliberate_boundary_refs"] for ref in (other, chain))


def test_scoped_selected_original_is_reverified_every_call(graph_case):
    c = graph_case
    scoped(c)
    Path(c["journal"].get(c["counter"]["record_id"])["path"]).write_text("tampered original")
    with pytest.raises(EvidenceError, match="identity|unavailable|hash"):
        scoped(c)


def test_branch_resolves_current_task_exact_roots_and_keeps_other_branch_controls(graph_case):
    c = graph_case
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
        scope={"branch_id": "fixed-core", "ordinary_hops": 0})
    assert c["decision"] in graph["scope_receipt"]["root_refs"]
    assert c["claim"] in graph["scope_receipt"]["root_refs"]
    assert graph["branches"] == c["checkpoint_content"]["branches"]


@pytest.mark.parametrize("scope", [{}, {"root_refs": []}, {"root_refs": [], "branch_id": "x"},
    {"branch_id": "missing"}, {"branch_id": "fixed-core", "ordinary_hops": 2},
    {"branch_id": "fixed-core", "ordinary_hops": True}])
def test_invalid_scope_fails_explicitly(graph_case, scope):
    with pytest.raises(EvidenceError, match="scope"):
        build_research_graph(graph_case["workspace"], checkpoint_ref=graph_case["checkpoint"], scope=scope)


def test_required_scope_cannot_succeed_by_omitting_counter_to_fit_cap(graph_case):
    c = graph_case
    for bounds in ({"max_nodes": 1, "max_bytes": 131072}, {"max_nodes": 128, "max_bytes": 4096}):
        with pytest.raises(EvidenceError, match="bound"):
            build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
                scope={"root_refs": [c["decision"]], "ordinary_hops": 0}, **bounds)
