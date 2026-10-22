"""Independent review counterexamples; all receipts are fixtures, no science runs."""
import copy

import pytest

from etalon.active.schema import digest
from etalon.research import method_contract as mc
from etalon.research import research_trace as rt
from etalon.research.evidence_refs import EvidenceError
from etalon.research.graph_memory import build_research_graph, node_identity
from test_method_contract import assess, contract, prepared
from test_research_graph_memory import graph_case
from test_research_trace import event


def test_review_undeclared_compiled_plugin_change_is_not_same_protocol(prepared):
    base = prepared[-1]
    candidate = copy.deepcopy(base)
    candidate["compiled_stages"][1]["plugin"] = "fixture.changed_scientific_method@9.0"
    candidate["compiled_digest"] = digest(candidate["compiled_stages"])
    result = assess(base, candidate, mode="same_protocol")
    assert result["status"] == "awaiting_evidence"
    assert result["differences"]


def test_review_undeclared_compiler_bytes_change_is_not_same_protocol(prepared):
    base = prepared[-1]
    candidate = copy.deepcopy(base)
    module = next(iter(candidate["modules"].values()))
    module["sha256"] = "f" * 64 if module["sha256"] != "f" * 64 else "e" * 64
    result = assess(base, candidate, mode="same_protocol")
    assert result["status"] == "awaiting_evidence"
    assert result["differences"]


def test_review_explicit_unchanged_default_is_not_an_immutable_value_change():
    specification = contract()
    parameter = specification["parameters"][0]
    parameter.update(required=False, immutable=True)
    result = mc.compare_parameters(
        specification,
        {"library": {}},
        {"library": {"batch_size": parameter["default"]}},
    )
    assert result["changes"] == []


@pytest.mark.parametrize("before,after", [("failed", "completed"), ("completed", "running")])
def test_review_terminal_attempt_cannot_be_overwritten_in_denominator(before, after):
    rows = [event(state=before, sequence=1), event(state=after, sequence=2)]
    with pytest.raises(EvidenceError):
        rt.project_trace(rows)


def test_review_input_change_cannot_hide_under_the_same_attempt():
    pending = event(state="running", sequence=1)
    completed = event(sequence=2)
    completed["detail"]["input_digest"] = "a-different-fixture-input"
    with pytest.raises(EvidenceError):
        rt.project_trace([pending, completed])


def test_review_reverse_counterclaim_enters_counter_selection(graph_case):
    c = graph_case
    counterclaim = c["save"]({
        "schema": "etalon-research-claim/1",
        "question": "Which route?",
        "statement": "Opposite fixture claim; inspect before inheriting the current conclusion",
        "epistemic_status": "interpretation",
        "subjects": ["SND1-fixture"],
        "conditions": ["fixture protocol"],
        "limitations": ["Not a science run"],
        "support_refs": [c["counter"]],
        "counter_refs": [c["claim"]],
        "method_refs": [],
        "supersedes": [],
    })
    graph = build_research_graph(c["publish"](), checkpoint_ref=c["checkpoint"])
    assert node_identity(counterclaim) in graph["counter_node_ids"]


def test_review_handwritten_comparison_cannot_hide_empty_coverage_as_ready():
    payload = {
        "schema": mc.COMPARISON,
        "supersedes": [],
        "mode": "same_protocol",
        "base_digest": "a" * 64,
        "candidate_digest": "b" * 64,
        "differences": [],
        "missing": [],
        "status": "ready_for_evaluation",
        "required_checks": ["chemical-identity"],
        "coverage": {"base": [], "candidate": []},
        "comparison_contexts": {
            side: {"input_digest": "c" * 64, "conditions_digest": "d" * 64}
            for side in ("base", "candidate")
        },
        "receipt_verification": {
            side: {"chemical-identity": "not_checked"}
            for side in ("base", "candidate")
        },
        "verifier_used": False,
        "verification_scope": "caller_attested_not_kernel_certified",
        "benefit_established": False,
    }
    with pytest.raises(EvidenceError, match="complete verified applicable coverage"):
        mc.validate_content(payload)
