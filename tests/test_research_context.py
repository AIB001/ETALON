"""Context receipt, bounded selection and continuation contract tests."""
from etalon.active.schema import canonical
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.graph_memory import node_identity
from etalon.research.research_context import build_research_context
import pytest

from test_research_graph_memory import graph_case


def test_entry_only_resume_carries_counter_scope_and_pause(graph_case):
    c = graph_case
    before = file_identity(c["store"].path)
    context = build_research_context(c["entry"], node_id=node_identity(c["decision"]),
        home_overrides={node_identity(c["counter"]): "Deep"})
    assert context["items"][0]["ref"] == c["decision"]
    assert c["counter"] in context["counterevidence"]["selected_refs"]
    assert context["counterevidence"]["status"] == "found"
    assert context["continuation"]["authority_state"] == "paused"
    claim = next(n for n in context["items"] if n["ref"] == c["claim"])
    assert claim["claim_scope"]["conditions"] == ["fixture protocol"]
    assert context["selection"]["strategy"] and context["source_watermarks"][0]["snapshot"]
    assert not context["automatic_retry"] and context["execution_permission"] == "not_granted"
    assert file_identity(c["store"].path) == before
    assert context == build_research_context(c["entry"], node_id=node_identity(c["decision"]),
        home_overrides={node_identity(c["counter"]): "Deep"})


def test_small_context_reports_omission_and_keeps_authority(graph_case):
    c = graph_case
    result = build_research_context(c["entry"], max_bytes=4096)
    assert len(canonical(result).encode()) <= 4096
    assert result["truncated"] and result["selection"]["excluded_by_budget"]
    assert result["continuation"]["authority_state"] == "paused"
    assert "unknowns" in result


def test_absent_counter_does_not_invent_quota(graph_case):
    c = graph_case
    # A separate checkpoint with only a raw fixture and a decision whose basis
    # has no counter links. It still warns about uncheckpointed original steps.
    plain = c["save"]({**c["step"], "task_id": "no-counter",
        "detail": {**c["step"]["detail"], "basis_refs": [c["evidence"]]}})
    cp = c["save"]({**c["checkpoint_content"], "task_id": "no-counter", "step_refs": [plain],
        "claim_refs": [], "counter_refs": [], "branches": [{"branch_id": "fixed-core", "state": "resolved",
        "basis_refs": [c["evidence"]], "next_read_refs": [], "reconsider_when": "New evidence"}]})
    result = build_research_context({"workspace": c["publish"](), "checkpoint_ref": cp})
    assert result["counterevidence"]["status"] == "none_in_checked_scope"
    assert result["counterevidence"]["selected_refs"] == []
    assert any("not proof" in unknown for unknown in result["unknowns"])


def test_unknown_node_not_silently_replaced(graph_case):
    with pytest.raises(EvidenceError, match="unavailable"):
        build_research_context(graph_case["entry"], node_id="not-the-current-node")


def test_pending_handle_is_observed_without_resubmit(graph_case, monkeypatch):
    import etalon.runtime.api as runtime
    c = graph_case
    calls = []
    def read(handle):
        calls.append(handle)
        return {"execution_state": "running", "verification": {}, "recovery": {}, "accounting_refs": []}
    monkeypatch.setattr(runtime, "read_action_result", read)
    handle = {"schema": "etalon-action-handle/1", "backend": "runtime", "workspace": str(c["entry"].parent),
              "request_id": "fixture-request", "job_id": "fixture-job", "plan_id": "fixture-plan", "node_id": "action"}
    cp = c["save"]({**c["checkpoint_content"], "pending_handles": [handle], "supersedes": [c["checkpoint"]]})
    result = build_research_context({"workspace": c["publish"](), "checkpoint_ref": cp})
    assert result["continuation"]["pending"] == [{"request_id": "fixture-request", "execution_state": "running"}]
    assert not result["automatic_retry"] and calls


def test_reverse_counter_context_preserves_direction_and_reports_budget_gap(graph_case):
    c = graph_case
    other = c["save"]({"schema": "etalon-research-claim/1", "question": "Which route?",
        "statement": "A second claim whose declared counter is the current claim",
        "epistemic_status": "interpretation", "subjects": ["SND1-fixture"], "conditions": ["fixture protocol"],
        "limitations": [], "support_refs": [c["counter"]], "counter_refs": [c["claim"]], "method_refs": [], "supersedes": []})
    entry = {"workspace": c["publish"](), "checkpoint_ref": c["checkpoint"]}
    full = build_research_context(entry)
    assert other in full["counterevidence"]["selected_refs"]
    assert any(e["from"] == node_identity(c["claim"]) and e["to"] == node_identity(other) and e["kind"] == "refutes"
               for e in full["relations"])
    assert not any(e["from"] == node_identity(other) and e["to"] == node_identity(c["claim"]) and e["kind"] == "refutes"
                   for e in full["relations"])
    small = build_research_context(entry, max_bytes=5000)
    assert not small["counterevidence"]["coverage_complete"]
    assert small["truncated"]
