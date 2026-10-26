"""Synthetic exact impact paths, bounded review and immutable reads."""
from pathlib import Path

import pytest

from etalon.active.schema import canonical
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.research_impact import build_research_impact
from test_research_graph_memory import graph_case
from test_declared_relations import nested_counter


def test_impact_reaches_declared_consumers_without_unrelated_branch_or_execution(graph_case):
    c = graph_case
    other = c["save"]({**c["step"], "branch_id": "unrelated", "input_refs": [c["unrelated"]],
        "detail": {**c["step"]["detail"], "basis_refs": [c["unrelated"]]}})
    c["publish"]()
    before = file_identity(c["store"].path)
    result = build_research_impact(c["entry"], changed_refs=[c["evidence"]], max_bytes=65536)
    affected = {canonical(row["ref"]): row for row in result["affected"]}
    assert canonical(c["claim"]) in affected and canonical(c["decision"]) in affected
    assert canonical(other) not in affected and canonical(c["unrelated"]) not in affected
    assert all(row["path"] for row in result["affected"])
    assert result["declared_scope_complete"] and not result["truncated"]
    assert result["scientific_status"] == "not_inferred"
    assert not result["automatic_invalidation"] and not result["rsi_triggered"]
    assert result["execution_permission"] == "not_granted" and not result["automatic_retry"]
    assert result["continuation"]["authority_state"] == "paused"
    assert file_identity(c["store"].path) == before and not c["store"].actions()


def test_new_nested_counter_notifies_old_claim_without_reversing_relation(graph_case):
    c = graph_case
    external = nested_counter(c)
    result = build_research_impact(c["entry"], changed_refs=[external], max_bytes=65536)
    row = next(row for row in result["affected"] if row["ref"] == c["decision"])
    assert row["path"][0] == {"kind": "refutes", "from_ref": c["decision"], "to_ref": external,
                               "basis_ref": external, "traversal": "reverse_review"}


def test_node_bound_keeps_exact_unexpanded_frontier(graph_case):
    c = graph_case
    result = build_research_impact(c["entry"], changed_refs=[c["evidence"]], max_nodes=1, max_bytes=65536)
    assert not result["affected"] and result["truncated"] and not result["declared_scope_complete"]
    assert result["boundaries"]
    assert any(row["ref"] == c["claim"] for row in result["boundaries"])
    assert all(row["original_verification"] == "metadata_identity_only" for row in result["boundaries"])


def test_impact_never_hides_required_paths_to_fit_transport(graph_case):
    c = graph_case
    for i in range(8):
        c["save"]({**c["step"], "branch_id": f"dependent-{i}"})
    c["publish"]()
    with pytest.raises(EvidenceError, match="byte bound"):
        build_research_impact(c["entry"], changed_refs=[c["evidence"]], max_bytes=4096)


def test_original_identity_rechecked_and_exact_changed_refs_required(graph_case):
    c = graph_case
    with pytest.raises(EvidenceError, match="identity|unavailable"):
        build_research_impact(c["entry"], changed_refs=[{**c["evidence"], "record_digest": "0" * 64}])
    build_research_impact(c["entry"], changed_refs=[c["evidence"]], max_bytes=65536)
    Path(c["journal"].get(c["evidence"]["record_id"])["path"]).write_text("tampered")
    with pytest.raises(EvidenceError, match="identity|hash|unavailable"):
        build_research_impact(c["entry"], changed_refs=[c["evidence"]], max_bytes=65536)


def test_restricted_impact_is_explicit_not_global_coverage(graph_case):
    c = graph_case
    result = build_research_impact(c["entry"], changed_refs=[c["evidence"]], max_bytes=65536,
        policy={"record_rules": [{"source_id": "science", "record_id": c["unrelated"]["record_id"],
                                    "allowed_purposes": []}]})
    assert not result["declared_scope_complete"]
    assert any(i["reason"] == "policy_narrowed_impact_universe" for i in result["source_issues"])
    assert "PRIVATE_SENTINEL" not in canonical(result)


def test_member_or_hash_qualifier_checked_even_for_same_record(graph_case):
    c = graph_case
    record = c["journal"].get(c["evidence"]["record_id"])
    forged = {**c["evidence"], "sha256": "0" * 64, "bytes": record["bytes"]}
    with pytest.raises(EvidenceError, match="identity"):
        build_research_impact(c["entry"], changed_refs=[c["evidence"], forged], max_bytes=65536)
