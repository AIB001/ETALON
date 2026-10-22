"""Research-route projections: fixture receipts, no scientific execution."""
import copy

import pytest

from etalon.research import research_trace as rt
from etalon.research.evidence_refs import EvidenceError, make_ref
from test_research_continuation import case, save, publish


def ref(label):
    return make_ref("fixture", "artifact-" + label, {"fixture": label})


def event(phase="execution", *, state="completed", sequence=1, attempt="attempt-1", reason="fixture outcome", key=None):
    details = {
        "execution": dict(tool_name="mcp__etalon__screen", call_id="call-" + str(sequence), input_digest="input-fixture",
            output_digest="output-fixture", duration_seconds=None, cost=None, reuse_of=ref("original") if state == "reused" else None,
            error="backend missing" if state == "failed" else None, completed_checks=[] if state == "failed" else ["identity"]),
        "declaration": dict(planned_checks=["identity"], alternatives=["repeat", "reuse"], choice="reuse", reconsider_when="inputs change"),
        "compiled": dict(revision_id="revision-fixture", declared_checks=["identity"], compiled_checks=["identity", "default-extra"], missing_checks=[]),
        "admission": dict(accepted_refs=[ref("result")] if state in {"accepted", "partial"} else [],
            rejected_refs=[ref("failure")] if state in {"rejected", "partial"} else [], criteria_ref=ref("criteria")),
    }
    return rt.trace_event(event_key=key or "event-" + str(sequence), sequence=sequence, task_id="fixture-task", branch_id="fixed-core",
        node_id="prepare", workspace="Quick", phase=phase, origin="declared" if phase == "declaration" else "observed",
        state=state, reason=reason, detail=details[phase], attempt_id=None if phase == "declaration" else attempt,
        config_ref=ref("config"), receipt_refs=[] if phase == "declaration" else [ref("receipt-" + str(sequence))],
        previous_refs=[ref("execution")] if phase == "admission" else [], evidence_refs=[ref("input")])


def test_failed_and_rejected_remain_in_attempt_denominator_reuse_is_not_repeat():
    rows = [event(state="failed"), event("admission", state="rejected", sequence=2),
            event(sequence=3, attempt="attempt-2"), event(sequence=4, state="reused", attempt="retrieval-1")]
    view = rt.project_trace(rows)
    assert view["summary"]["execution_attempts"] == 2
    assert view["summary"]["attempt_states"]["failed"] == 1
    assert view["summary"]["rejected_admissions"] == 1
    assert view["summary"]["reuses"] == 1
    assert len(view["tools"]) == 3
    assert view["receipt_verification"] == "not_verified_by_projection"


def test_duplicate_delivery_dedup_but_conflicting_event_rejected():
    row = event()
    assert rt.project_trace([row, row])["summary"]["events"] == 1
    changed = copy.deepcopy(row)
    changed["reason"] = "different content"
    with pytest.raises(EvidenceError, match="conflicting"):
        rt.project_trace([row, changed])


def test_pending_then_terminal_updates_count_one_attempt():
    rows = [event(state="running"), event(sequence=2)]
    view = rt.project_trace(rows)
    assert view["summary"]["execution_attempts"] == 1
    assert view["summary"]["attempt_states"]["completed"] == 1
    assert len(view["tools"]) == 2


def test_compiler_may_add_default_checks_but_cannot_conceal_missing_checks():
    row = event("compiled", state="ready")
    assert "default-extra" in row["detail"]["compiled_checks"]
    broken = copy.deepcopy(row)
    broken["detail"]["compiled_checks"] = []
    with pytest.raises(EvidenceError, match="coverage"):
        rt.validate_content(broken)
    broken["detail"]["missing_checks"] = ["identity"]
    with pytest.raises(EvidenceError, match="missing checks"):
        rt.validate_content(broken)
    broken["state"] = "blocked"
    assert rt.validate_content(broken)["state"] == "blocked"


def test_agent_declaration_cannot_be_observed_execution_without_receipt():
    row = event("declaration", state="skipped")
    row["origin"] = "observed"
    with pytest.raises(EvidenceError, match="declared"):
        rt.validate_content(row)
    row = event()
    row["receipt_refs"] = []
    with pytest.raises(EvidenceError, match="receipts"):
        rt.validate_content(row)


def test_html_escapes_all_user_material_and_exposes_provenance_limit():
    output = rt.render_trace_html([event(reason='<script>alert("x")</script> & failure')], title='<img src=x onerror="x">')
    assert "<script>" not in output
    assert "<img src=x" not in output
    assert "&lt;script&gt;" in output
    assert "not authentication" in output
    assert "not verified by this projection" in output


def test_one_attempt_cannot_be_both_reuse_and_fresh_execution():
    with pytest.raises(EvidenceError, match="both"):
        rt.project_trace([event(), event(sequence=2, state="reused")])


def test_retry_cannot_erase_failed_attempt_from_denominator():
    with pytest.raises(EvidenceError, match="retry needs a new attempt"):
        rt.project_trace([event(state="failed"), event(sequence=2, state="completed")])


@pytest.mark.parametrize("identity", ["tool", "input", "config", "method"])
def test_state_updates_cannot_change_attempt_identity(identity):
    pending, completed = event(state="running"), event(sequence=2)
    if identity == "tool":
        completed["detail"]["tool_name"] = "another_tool"
    elif identity == "input":
        completed["detail"]["input_digest"] = "changed-input"
    elif identity == "config":
        completed["config_ref"] = ref("other-config")
    else:
        completed["method_refs"] = [ref("other-method")]
    with pytest.raises(EvidenceError, match="identity changed"):
        rt.project_trace([pending, completed])


def test_unknown_cost_stays_unknown_and_negative_duration_rejected():
    row = event()
    assert rt.project_trace([row])["tools"][0]["detail"]["cost"] is None
    row["detail"]["duration_seconds"] = -1
    with pytest.raises(EvidenceError, match="duration"):
        rt.validate_content(row)


def test_admission_cannot_discard_rejected_evidence():
    row = event("admission", state="partial")
    row["detail"]["rejected_refs"] = []
    with pytest.raises(EvidenceError, match="both portions"):
        rt.validate_content(row)


def test_journal_roundtrip_and_admission_attempt_link(case):
    from etalon.research.continuation import read_content
    journal, real_ref, _, _ = case
    def local(value):
        if isinstance(value, dict):
            if value.get("schema") == "etalon-evidence-ref/1":
                return real_ref
            return {k: local(v) for k, v in value.items()}
        if isinstance(value, list):
            return [local(v) for v in value]
        return value
    executed = local(event(state="failed"))
    execution_ref = save(case, executed)
    admission = local(event("admission", state="rejected", sequence=2))
    admission["previous_refs"] = [execution_ref]
    saved = save(case, admission)
    assert read_content(publish(case), saved) == admission
    before = len(journal.store.events())
    assert save(case, admission) == saved
    assert len(journal.store.events()) == before
    admission["attempt_id"] = "another-attempt"
    with pytest.raises(EvidenceError, match="different attempt"):
        save(case, admission)
