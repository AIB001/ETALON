"""Bounded reference/counter audit; fixtures are not scientific evidence."""
import copy
from pathlib import Path

import pytest

from etalon.active.schema import canonical, digest
from etalon.research.continuation import resume_research
from etalon.research.continuation_pages import audit_closure_pages
from etalon.research.evidence import StaleCursorError
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.research_context import build_research_context
from test_research_graph_memory import graph_case
from test_scope_adversarial import claim


def pages(c, *, cap=4096, limit=3, policy=None):
    result, cursor, entry = [], None, c["entry"]
    for _ in range(300):
        page = build_research_context(entry, view="manifest", section="closure",
            cursor=cursor, limit=limit, max_bytes=cap, policy=policy)
        assert len(canonical(page).encode()) <= cap
        assert not page["coverage"]["all_pages_original_verification_complete"]
        assert page["execution_permission"] == "not_granted" and not page["automatic_retry"]
        result.append(page)
        cursor, entry = page["next_cursor"], page["read_context"]
        if cursor is None:
            return result
    raise AssertionError("closure pages did not terminate")


def audit(c, transcript, policy=None):
    first = transcript[0]
    return audit_closure_pages(c["workspace"], c["checkpoint"],
        {"read_context": first["read_context"], "entry_status": first["entry_status"]},
        transcript, policy=policy)


def test_consecutive_pages_close_cross_task_reverse_counters_read_only(graph_case):
    c = graph_case
    outside = claim(c, "outside-selected-task", counters=[c["claim"]], support=[c["unrelated"]])
    last = claim(c, "transitive-outside-task", counters=[outside])
    c["publish"]()
    before = file_identity(c["store"].path)
    transcript = pages(c, cap=65536)
    rows = [i["value"] for p in transcript for i in p["items"]]
    assert {canonical(r["ref"]) for r in rows if r["kind"] == "reference"} >= {
        canonical(r) for r in (outside, last, c["unrelated"])}
    assert any(r["kind"] == "refutes" and r["from_ref"] == c["claim"] and r["to_ref"] == outside for r in rows)
    receipt = audit(c, transcript)
    assert receipt["declared_reference_and_counter_closure_complete"]
    assert receipt["original_bodies_read_by_host"] == "not_established"
    assert not receipt["next_read"]
    partial = audit(c, transcript[:1])
    assert not partial["declared_reference_and_counter_closure_complete"]
    assert partial["next_read"]["cursor"] == transcript[0]["next_cursor"]
    assert file_identity(c["store"].path) == before and not c["store"].actions()


def test_minimum_transport_preserves_fragments_and_all_refs(graph_case):
    transcript = pages(graph_case, cap=4096, limit=50)
    assert audit(graph_case, transcript)["declared_reference_and_counter_closure_complete"]


@pytest.mark.parametrize("change", ["last_only", "missing", "repeated", "reordered", "removed_verification"])
def test_cursor_or_final_page_cannot_prove_missing_prior_pages(graph_case, change):
    transcript = pages(graph_case, cap=65536, limit=2)
    damaged = copy.deepcopy(transcript)
    if change == "last_only":
        damaged = damaged[-1:]
    elif change == "missing":
        damaged.pop(1)
    elif change == "repeated":
        damaged.insert(1, damaged[0])
    elif change == "reordered":
        damaged[0], damaged[1] = damaged[1], damaged[0]
    else:
        damaged[0]["coverage"]["original_verification_indices"] = []
        damaged[0]["context_id"] = "research-context-" + digest({k: v for k, v in damaged[0].items() if k != "context_id"})
    with pytest.raises(EvidenceError, match="(transcript|verification)"):
        audit(graph_case, damaged)


def test_source_and_implementation_changes_reject_old_cursor(graph_case, monkeypatch):
    c = graph_case
    first = pages(c, limit=2)[0]
    import etalon.research.continuation_pages as module
    original_identity = module.implementation_identity
    monkeypatch.setattr(module, "implementation_identity", lambda: "changed-implementation")
    with pytest.raises(StaleCursorError, match="stale"):
        build_research_context(c["entry"], view="manifest", section="closure", cursor=first["next_cursor"])
    monkeypatch.setattr(module, "implementation_identity", original_identity)
    claim(c, "newly-published-counter", counters=[c["claim"]])
    c["publish"]()
    with pytest.raises(StaleCursorError, match="stale"):
        build_research_context(c["entry"], view="manifest", section="closure", cursor=first["next_cursor"])


def test_tampered_outside_counter_body_fails_on_its_verification_page(graph_case):
    c = graph_case
    outside = claim(c, "outside-counter", counters=[c["claim"]], support=[c["unrelated"]])
    c["publish"]()
    Path(c["journal"].get(c["unrelated"]["record_id"])["path"]).write_text("tampered original")
    with pytest.raises(EvidenceError, match="(hash|identity|unavailable)"):
        pages(c)


def test_policy_narrowing_never_claims_full_counter_coverage(graph_case):
    c = graph_case
    policy = {"record_rules": [{"source_id": "science", "record_id": c["unrelated"]["record_id"],
        "allowed_purposes": ["evaluation"]}]}
    receipt = audit(c, pages(c, policy=policy), policy=policy)
    assert receipt["navigation_exhausted"]
    assert receipt["coverage_issue_count"]
    assert not receipt["declared_reference_and_counter_closure_complete"]


def test_legacy_truncation_remains_separate_from_explicit_paged_closure(graph_case, monkeypatch):
    c = graph_case
    import etalon.research.continuation as legacy
    monkeypatch.setattr(legacy, "MAX_RESUME_RECORDS", 2)
    original = resume_research(c["workspace"], c["checkpoint"])
    assert any(i["reason"] == "reference_walk_truncated" for i in original["issues"])
    receipt = audit(c, pages(c))
    assert receipt["declared_reference_and_counter_closure_complete"]
    assert any(i["reason"] == "reference_walk_truncated" for i in resume_research(c["workspace"], c["checkpoint"])["issues"])


def test_metadata_bound_fails_explicitly_instead_of_complete_truncation(graph_case, monkeypatch):
    import etalon.research.continuation_pages as module
    monkeypatch.setattr(module, "MAX_RECORDS", 2)
    with pytest.raises(EvidenceError, match="index bound"):
        pages(graph_case)
