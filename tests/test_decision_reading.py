"""Synthetic reading contracts; no science execution or benefit inference."""
import base64
import copy
import json
from pathlib import Path

import pytest

from etalon.active.schema import canonical
from etalon.research.decision_reading import build_decision_reading
from etalon.research.evidence import StaleCursorError, read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.graph_memory import node_identity
from test_research_graph_memory import graph_case


def pages(entry, **options):
    rows, fragments, outputs = [], {}, []
    cursor = None
    while True:
        page = build_decision_reading(entry, question="Which exact decision can change?", cursor=cursor, **options)
        assert len(canonical(page).encode()) <= options.get("max_bytes", 16384)
        outputs.append(page)
        for item in page["items"]:
            if "value" in item:
                rows.append(item["value"])
            else:
                fragment = item["fragment"]
                data = fragments.setdefault(item["index"], bytearray())
                assert len(data) == fragment["offset"]
                data.extend(base64.b64decode(fragment["content"]))
                if fragment["next_offset"] is None:
                    assert len(data) == fragment["total_bytes"]
                    rows.append(json.loads(data))
        cursor = page["next_cursor"]
        if cursor is None:
            assert len(rows) == page["total_count"]
            return rows, outputs
        assert len(outputs) < 100


def test_default_selection_is_concrete_readonly_and_preserves_counter_direction(graph_case):
    c = graph_case
    before = file_identity(c["store"].path)
    rows, outputs = pages(c["entry"], relevance=[{
        "ref": c["decision"], "why": "Host asks whether current reuse remains justified",
        "affects_decision": "retain reuse or reopen"}])
    plan = outputs[0]
    reads = [row for row in rows if "ref" in row]
    assert len({node_identity(row["ref"]) for row in reads}) == len(reads)
    decision = next(row for row in reads if row["ref"] == c["decision"])
    assert decision["decision"]["choice"] == "reuse"
    assert decision["host_relevance"]["affects_decision"] == "retain reuse or reopen"
    assert decision["relevance_provenance"]["host_declared"]
    assert decision["original_read"]["view"] == "artifact"
    assert all(row["source_scope"]["source_id"] == row["ref"]["source_id"] for row in reads)
    assert any(row.get("from_ref") == c["counter"] and row["to_ref"] == c["claim"] for row in rows)
    assert plan["counterevidence"]["reference_coverage_complete"]
    assert plan["continuation"]["authority_state"] == "paused"
    assert plan["execution_permission"] == "not_granted" and not plan["automatic_retry"]
    assert plan["stop"]["assessment"] == "host_judgment_required"
    assert not plan["stop"]["page_exhaustion_is_comprehension"]
    original = read_evidence(read_context=plan["read_context"], ref=decision["ref"], **decision["original_read"])
    assert json.loads(base64.b64decode(original["fragment"]["content"])) == c["step"]
    assert file_identity(c["store"].path) == before and not c["store"].actions()


def test_counter_other_branch_is_never_top_k_filtered(graph_case):
    c = graph_case
    contrary = c["save"]({"schema": "etalon-research-claim/1", "question": "Other task",
        "statement": "Cross-task conflict", "epistemic_status": "interpretation", "subjects": [],
        "conditions": [], "limitations": [], "support_refs": [], "counter_refs": [c["decision"]],
        "method_refs": [], "supersedes": []})
    c["publish"]()
    rows, outputs = pages(c["entry"], scope={"root_refs": [c["decision"]], "ordinary_hops": 0}, limit=1)
    assert len(outputs) > 1 and outputs[0]["next_cursor"]
    assert any(row.get("ref") == contrary for row in rows)
    assert any(row.get("from_ref") == c["decision"] and row.get("to_ref") == contrary for row in rows)
    assert outputs[-1]["counterevidence"]["original_bodies_read_by_host"] == "not_established"


def test_scope_branch_change_and_conditions_invalidate_cursor(graph_case):
    c = graph_case
    first = build_decision_reading(c["entry"], question="choose", limit=1,
        scope={"branch_id": "fixed-core"})
    for options in ({"scope": {"root_refs": [c["evidence"]]}},
                    {"scope": {"branch_id": "fixed-core"}, "conditions": {"state": "neutral"}},
                    {"scope": {"branch_id": "fixed-core"}, "reading_mode": "status"}):
        with pytest.raises(StaleCursorError):
            build_decision_reading(c["entry"], question="choose", cursor=first["next_cursor"], **options)


def test_new_or_changed_source_invalidates_cursor(graph_case):
    c = graph_case
    first = build_decision_reading(c["entry"], question="choose", limit=1)
    c["save"]({**c["step"], "branch_id": "other", "reason": "new permitted record"})
    c["publish"]()
    with pytest.raises(StaleCursorError):
        build_decision_reading(c["entry"], question="choose", cursor=first["next_cursor"])
    with pytest.raises(EvidenceError, match="stale_read_context"):
        build_decision_reading(first["read_context"], question="choose", cursor=first["next_cursor"])
    second = build_decision_reading(c["entry"], question="choose", limit=1)
    external = c["entry"].parent / "empty.json"
    external.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": []}))
    ws = json.loads(c["workspace"].read_text())
    ws["sources"].append({"source_id": "new-source", "kind": "artifact_manifest", "location": str(external),
                          "identity": {"kind": "snapshot_sha256", "value": file_identity(external)["sha256"]}})
    c["workspace"].write_text(json.dumps(ws))
    with pytest.raises(StaleCursorError):
        build_decision_reading(c["entry"], question="choose", cursor=second["next_cursor"])


@pytest.mark.parametrize("scope", [{"branch_id": "unknown"}, {"task_id": "guess"},
    {"root_refs": []}, {"root_refs": [], "branch_id": "fixed-core"}, {"root_refs": [None]}])
def test_missing_and_unknown_scope_fail_closed(graph_case, scope):
    with pytest.raises(EvidenceError):
        build_decision_reading(graph_case["entry"], question="choose", scope=scope)


def test_pinned_purpose_policy_and_sealed_visibility(graph_case):
    c = graph_case
    policy = {"record_rules": [{"source_id": "science", "record_id": c["unrelated"]["record_id"],
        "allowed_purposes": ["evaluation"]}]}
    first = build_decision_reading(c["entry"], question="choose", policy=policy)
    with pytest.raises(EvidenceError, match="purpose cannot change"):
        build_decision_reading(first["read_context"], question="choose", purpose="evaluation")
    research = build_decision_reading(first["read_context"], question="choose")
    assert not research["counterevidence"]["reference_coverage_complete"]
    # The pinned receipt repeats the caller's policy; it must not reveal content.
    assert "PRIVATE_SENTINEL" not in canonical(research)
    assert c["unrelated"]["record_id"] not in canonical(research["items"])
    with pytest.raises(EvidenceError, match="policy cannot change"):
        build_decision_reading(first["read_context"], question="choose", policy={})
    denied = {"record_rules": [{"source_id": "science", "record_id": c["counter"]["record_id"],
                               "allowed_purposes": ["evaluation"]}]}
    with pytest.raises(EvidenceError, match="checkpoint unavailable"):
        build_decision_reading(c["entry"], question="choose", policy=denied)


def test_original_corruption_is_not_bypassed_by_status_mode(graph_case):
    c = graph_case
    Path(c["journal"].get(c["counter"]["record_id"])["path"]).write_text("corrupted original")
    with pytest.raises(EvidenceError, match="identity|unavailable|hash"):
        build_decision_reading(c["entry"], question="status", reading_mode="status")


@pytest.mark.parametrize("conditions, expected", [(None, "unknown"),
    ({"state": None}, "unknown"), ({"state": "neutral"}, "match"), ({"state": "charged"}, "conflict")])
def test_experience_conditions_are_retained_even_when_conflicting(graph_case, monkeypatch, conditions, expected):
    # Projection-only synthetic experience: tests delivery, never RSI validity.
    import etalon.research.decision_reading as module
    real = module.build_research_graph
    def inject(*args, **kwargs):
        graph = copy.deepcopy(real(*args, **kwargs))
        node = next(n for n in graph["nodes"] if n["ref"] == graph_case["decision"])
        node["experience"] = {"conditions": {"state": "neutral"}, "invalid_when": "charged state",
            "outcome": "retain", "scope": "fixture", "lesson": "not scientific evidence",
            "supersedes": [graph_case["decision"]]}
        return graph
    monkeypatch.setattr(module, "build_research_graph", inject)
    rows, _ = pages(graph_case["entry"], conditions=conditions)
    item = next(row for row in rows if row.get("ref") == graph_case["decision"])
    assert item["applicability"]["status"] == expected
    assert item["applicability"]["invalid_when"] == "charged state"
    assert item["activation"] == "superseded_in_selected_scope"
    assert not item["applicability"]["automatic_adoption"]


def test_lossless_fragment_pages_and_stop_remains_unknown(graph_case):
    c = graph_case
    annotation = {"ref": c["decision"], "why": "理由" * 1500, "affects_decision": "fixture choice"}
    rows, outputs = pages(c["entry"], relevance=[annotation], max_bytes=4096)
    assert any("fragment" in item for page in outputs for item in page["items"])
    assert next(row for row in rows if row.get("ref") == c["decision"])["host_relevance"] == annotation
    assert all(page["stop"]["assessment"] == "host_judgment_required" for page in outputs)


def test_status_and_diagnosis_obligations_are_distinct(graph_case):
    c = graph_case
    status = build_decision_reading(c["entry"], question="current status", reading_mode="status")
    assert status["stop"]["status_may_stop_early"]
    changed = build_decision_reading(c["entry"], question="current status", reading_mode="status", decision_changed=True)
    assert not changed["stop"]["status_may_stop_early"]
    rows, outputs = pages(c["entry"], reading_mode="diagnosis", scope={"root_refs": [c["decision"]], "ordinary_hops": 0})
    boundary = [row for row in rows if row["section"] == "optional_detail"]
    assert boundary and all(row["original_read"] and row["expand"]["scope"]["root_refs"] for row in boundary)
    assert "unknown cause" in outputs[0]["stop"]["diagnosis_condition"]
    assert outputs[0]["stop"]["gap_count"] > 0
    with pytest.raises(EvidenceError, match="4096..16384"):
        build_decision_reading(c["entry"], question="choose", max_bytes=32768)


def test_relevance_cannot_smuggle_unselected_reference(graph_case):
    c = graph_case
    with pytest.raises(EvidenceError, match="select exact roots"):
        build_decision_reading(c["entry"], question="choose", relevance=[{
            "ref": c["unrelated"], "why": "host rationale", "affects_decision": "out of selected scope"}])


def test_discovered_experience_is_conditional_but_counter_to_root_is_required(graph_case):
    c = graph_case
    # Manifest records are explicit engineering fixtures, not validated RSI outcomes.
    experience = {"schema": "etalon-method-experience/1", "task_id": "snd1-fixture",
        "conditions": {"state": "neutral"}, "invalid_when": "charged", "outcome": "unresolved",
        "capability_id": "fixture", "scope": "fixture", "lesson": "UNRELATED_DISCOVERED_LESSON",
        "support_refs": [], "counter_refs": [], "supersedes": []}
    conflicting = {**experience, "lesson": "CHALLENGES_EXACT_ROOT", "counter_refs": [c["decision"]]}
    sealed = {**experience, "role": "sealed_holdout", "lesson": "SEALED_SENTINEL"}
    bodies = [experience, conflicting, sealed]
    manifest = c["entry"].parent / "experiences.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [
        {"record_id": str(i), "record_type": "experience", "record": body, "visibility": "released"}
        for i, body in enumerate(bodies)]}))
    config = json.loads(c["workspace"].read_text())
    config["sources"].append({"source_id": "experience-fixture", "kind": "artifact_manifest",
        "location": str(manifest), "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}})
    c["workspace"].write_text(json.dumps(config))
    rows, outputs = pages(c["entry"], conditions={"state": "charged"})
    discovered = next(r for r in rows if r.get("ref") == make_ref("experience-fixture", "0", experience))
    challenge = next(r for r in rows if r.get("ref") == make_ref("experience-fixture", "1", conflicting))
    checkpoint = next(r for r in rows if r.get("ref") == c["checkpoint"])
    assert discovered["read_requirement"] == "conditional_host_relevance_review"
    assert discovered["applicability"]["status"] == "conflict"
    assert challenge["read_requirement"] == "required_before_changed_decision_or_causal_claim"
    assert challenge["obligation_basis"] == "counter_challenges_selected_basis"
    assert checkpoint["metadata_requirement"] == "current_controls_required"
    assert checkpoint["read_requirement"] == "conditional_host_relevance_review"
    assert "SEALED_SENTINEL" not in canonical(rows)
    assert not outputs[0]["counterevidence"]["reference_coverage_complete"]
    assert any(g["reason"] == "discovered_context_relevance_requires_host_review"
               for row in rows if row["section"] == "gaps" for g in row["items"])


def test_applicability_never_promotes_empty_or_undeclared_dimensions():
    from etalon.research.decision_reading import _applicability
    empty = _applicability({"conditions": {}, "invalid_when": "unknown"}, {"state": "neutral"})
    missing = _applicability({"conditions": {"state": "neutral"}}, {"state": "neutral", "endpoint": "new"})
    assert empty["status"] == missing["status"] == "unknown"
    assert missing["unassessed_host_dimensions"] == ["endpoint"]
    assert missing["scientific_applicability"] == "not_established"


def test_boundary_basis_counter_scope_is_unknown_until_explicitly_expanded(graph_case):
    c = graph_case
    claim = c["save"]({"schema": "etalon-research-claim/1", "question": "New basis",
        "statement": "Fixture boundary basis", "epistemic_status": "interpretation", "subjects": [],
        "conditions": [], "limitations": [], "support_refs": [], "counter_refs": [c["unrelated"]],
        "method_refs": [], "supersedes": []})
    decision = c["save"]({**c["step"], "branch_id": "boundary", "input_refs": [claim],
        "detail": {**c["step"]["detail"], "basis_refs": [claim]}})
    c["publish"]()
    rows, outputs = pages(c["entry"], scope={"root_refs": [decision], "ordinary_hops": 0})
    assert outputs[0]["counterevidence"]["decision_counter_closure"] == "unresolved_boundary"
    boundary = next(row for row in rows if row.get("ref") == claim)
    assert boundary["section"] == "optional_detail"
    assert boundary["read_requirement"] == "required_before_changed_decision_or_causal_claim"
    expanded, _ = pages(c["entry"], scope=boundary["expand"]["scope"])
    counter = next(row for row in expanded if row.get("ref") == c["unrelated"])
    assert counter["read_requirement"] == "required_before_changed_decision_or_causal_claim"
