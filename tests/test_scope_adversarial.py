"""Independent scoped-read counterexamples over synthetic Journal artifacts.

These are engineering contract cases, not new scientific runs or transfer tests.
Only public graph/context entry points are asserted; no implementation is patched.
"""
from pathlib import Path
import json

import pytest

from etalon.active.schema import canonical, digest
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.graph_memory import build_research_graph, node_identity
from etalon.research.research_context import build_research_context

from test_research_graph_memory import graph_case


def scope(ref, hops=0):
    return {"root_refs": [ref], "ordinary_hops": hops}


def claim(case, name, *, counters=(), support=(), **extra):
    return case["save"]({
        "schema": "etalon-research-claim/1", "task_id": "other-task-" + name,
        "question": "Synthetic adversarial relation", "statement": name,
        "epistemic_status": "interpretation", "subjects": ["fixture"],
        "conditions": ["synthetic only"], "limitations": ["No science claim"],
        "support_refs": list(support), "counter_refs": list(counters),
        "method_refs": [], "supersedes": [], **extra,
    })


def relation_pairs(graph):
    return {(node_identity(e["from_ref"]), node_identity(e["to_ref"]))
            for e in graph["scope_receipt"]["counter_relations"]}


def reachable_refs(value):
    refs = []
    def walk(item):
        if isinstance(item, dict):
            if item.get("schema") == "etalon-evidence-ref/1":
                refs.append(item)
            else:
                for child in item.values():
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value)
    return refs


def test_scope_preserves_cross_task_counter_chain_and_original_direction(graph_case):
    c = graph_case
    other = claim(c, "cross-task-counter-target", counters=[c["claim"]])
    last = claim(c, "cross-task-chain-target", counters=[other])
    graph = build_research_graph(c["publish"](), checkpoint_ref=c["checkpoint"],
                                 scope=scope(c["claim"]))
    expected = {(node_identity(c["counter"]), node_identity(c["claim"])),
                (node_identity(c["claim"]), node_identity(other)),
                (node_identity(other), node_identity(last))}
    assert expected <= relation_pairs(graph)
    assert not {(b, a) for a, b in expected} & relation_pairs(graph)
    assert {node_identity(r) for r in (c["claim"], c["counter"], other, last)} <= {
        node_identity(r) for r in graph["scope_receipt"]["counter_endpoint_refs"]}
    assert graph["scope_receipt"]["counter_reference_coverage_complete"]
    assert graph["scope_receipt"]["global_reference_coverage_complete"] is False


def test_nested_counter_declaration_is_not_downgraded_to_ordinary_boundary(graph_case):
    c = graph_case
    nested = claim(c, "nested-counter", nested_note={"counter_refs": [c["claim"]]})
    graph = build_research_graph(c["publish"](), checkpoint_ref=c["checkpoint"],
                                 scope=scope(c["claim"]))
    assert (node_identity(c["claim"]), node_identity(nested)) in relation_pairs(graph)
    assert graph["scope_receipt"]["counter_reference_coverage_complete"]


@pytest.mark.parametrize("forged", ["digest", "bytes", "member"])
def test_forged_root_assertion_cannot_be_ignored_or_reported_verified(graph_case, forged):
    c = graph_case
    ref = dict(c["counter"])
    if forged == "digest":
        ref["record_digest"] = "0" * 64
    elif forged == "bytes":
        ref.update(sha256="0" * 64, bytes=1, selector="bytes")
    else:
        ref["member"] = "not-a-published-member"
    with pytest.raises(EvidenceError):
        build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], scope=scope(ref))


def test_duplicate_node_identity_does_not_erase_conflicting_artifact_assertion(graph_case):
    c = graph_case
    good = c["counter"]
    forged = {**good, "sha256": "0" * 64, "bytes": 1, "selector": "bytes"}
    assert node_identity(good) == node_identity(forged)
    with pytest.raises(EvidenceError):
        build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
            scope={"root_refs": [good, forged], "ordinary_hops": 0})


def test_hidden_reverse_claim_forbids_complete_search_without_leaking_identity(graph_case):
    c = graph_case
    hidden = claim(c, "HIDDEN_REVERSE_CLAIM_SENTINEL", counters=[c["claim"]], support=[c["unrelated"]])
    c["publish"]()
    policy = {"record_rules": [{"source_id": "science", "record_id": c["unrelated"]["record_id"],
                                 "allowed_purposes": ["evaluation"]}]}
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
                                 scope=scope(c["claim"]), policy=policy)
    receipt = graph["scope_receipt"]
    assert not receipt["counter_search_complete"]
    assert not receipt["counter_reference_coverage_complete"]
    assert hidden["record_id"] not in canonical(graph)
    assert "HIDDEN_REVERSE_CLAIM_SENTINEL" not in canonical(graph)
    assert c["unrelated"]["record_id"] not in canonical(graph)


def test_missing_published_source_forbids_counter_absence_claim(graph_case):
    c = graph_case
    config = json.loads(c["workspace"].read_text(encoding="utf-8"))
    config["sources"].append({"source_id": "unavailable-external", "kind": "artifact_manifest",
        "location": "missing-source.json", "identity": {"kind": "snapshot_sha256", "value": "0" * 64}})
    c["workspace"].write_text(json.dumps(config), encoding="utf-8")
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], scope=scope(c["claim"]))
    assert not graph["scope_receipt"]["counter_search_complete"]
    assert not graph["scope_receipt"]["counter_reference_coverage_complete"]


def test_manifest_record_allowlist_is_not_an_unqualified_complete_counter_search(graph_case):
    c = graph_case
    hidden = claim(c, "EXCLUDED_SOURCE_RECORD_SENTINEL", counters=[c["claim"]])
    c["publish"]()
    config = json.loads(c["workspace"].read_text(encoding="utf-8"))
    config["sources"][0]["record_ids"] = [c[key]["record_id"] for key in
        ("evidence", "counter", "unrelated", "claim", "decision", "checkpoint")]
    c["workspace"].write_text(json.dumps(config), encoding="utf-8")
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], scope=scope(c["claim"]))
    assert not graph["scope_receipt"]["counter_search_complete"]
    assert not graph["scope_receipt"]["counter_reference_coverage_complete"]
    assert hidden["record_id"] not in canonical(graph)


@pytest.mark.parametrize("corruption", ["digest", "member"])
def test_reverse_counter_with_wrong_record_digest_is_reported_as_unresolved(graph_case, corruption):
    c = graph_case
    broken_target = {**c["claim"], **({"record_digest": "0" * 64} if corruption == "digest"
                                    else {"member": "unknown-member"})}
    body = {"schema": "etalon-research-claim/1", "question": "Fixture broken reverse metadata",
        "statement": "Counter declaration with an unresolved target identity", "epistemic_status": "interpretation",
        "subjects": ["fixture"], "conditions": [], "limitations": [], "support_refs": [],
        "counter_refs": [broken_target], "method_refs": [], "supersedes": []}
    manifest = c["entry"].parent / "broken-reverse.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [
        {"record_id": "broken-reverse", "record_type": "claim", "record": body, "visibility": "released"}]}),
        encoding="utf-8")
    config = json.loads(c["workspace"].read_text(encoding="utf-8"))
    config["sources"].append({"source_id": "broken-reverse", "kind": "artifact_manifest",
        "location": str(manifest), "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}})
    c["workspace"].write_text(json.dumps(config), encoding="utf-8")
    try:
        graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], scope=scope(c["claim"]))
    except EvidenceError:
        return
    assert not graph["scope_receipt"]["counter_search_complete"]
    assert not graph["scope_receipt"]["counter_reference_coverage_complete"]


def test_branch_name_collision_does_not_select_another_task(graph_case):
    c = graph_case
    unrelated = c["save"]({**c["step"], "task_id": "other-task",
        "reason": "OTHER_TASK_SAME_BRANCH_SENTINEL", "input_refs": [],
        "detail": {**c["step"]["detail"], "basis_refs": [c["evidence"]]}})
    graph = build_research_graph(c["publish"](), checkpoint_ref=c["checkpoint"],
        scope={"branch_id": "fixed-core", "ordinary_hops": 0})
    assert c["decision"] in graph["scope_receipt"]["root_refs"]
    assert unrelated not in graph["scope_receipt"]["root_refs"]
    assert "OTHER_TASK_SAME_BRANCH_SENTINEL" not in canonical(graph)


def test_explicit_scope_reaches_node_excluded_by_broad_projection(graph_case):
    c = graph_case
    candidates = [c["save"]({**c["step"], "branch_id": f"noise-{i}",
        "reason": f"isolated fixture {i}", "input_refs": [],
        "detail": {**c["step"]["detail"], "basis_refs": [c["evidence"]]}}) for i in range(132)]
    c["publish"]()
    broad = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
                                max_nodes=128, max_bytes=262144)
    included = {n["node_id"] for n in broad["nodes"]}
    excluded = next(r for r in candidates if node_identity(r) not in included)
    assert broad["truncated"]
    context = build_research_context(c["entry"], view="node", node_id=node_identity(excluded),
                                    scope=scope(excluded), max_bytes=32768)
    assert any(n["ref"] == excluded for n in context["items"])
    assert context["scope_receipt"]["reference_coverage_complete"]
    assert context["scope_receipt"]["global_reference_coverage_complete"] is False


def test_summary_conserves_counter_references_when_excerpts_are_omitted(graph_case):
    c = graph_case
    negatives = [claim(c, f"negative target {i}", counters=[c["claim"]]) for i in range(6)]
    c["publish"]()
    context = build_research_context(c["entry"], view="summary", scope=scope(c["claim"]), max_bytes=65536)
    expected = {node_identity(r) for r in negatives + [c["claim"], c["counter"]]}
    indexed = {item["node_id"] for item in context["reference_index"]}
    assert expected <= indexed
    assert len(context["items"]) <= 4
    assert context["scope_receipt"]["counter_reference_coverage_complete"]
    assert not context["counterevidence"]["coverage_complete"]
    assert not context["selection"]["coverage_complete"]
    assert context["scope_receipt"]["global_reference_coverage_complete"] is False
    assert "excerpt_coverage" in context
    directed = [e for e in context["relations"] if e["kind"] == "refutes"]
    assert len(directed) >= 7
    assert context["scope_receipt"]["counter_relation_count"] == len(directed)
    assert all(e[key] in indexed for e in directed for key in ("from", "to", "basis_node_id"))


def test_compact_context_counter_and_boundary_relations_expand_without_loss(graph_case):
    c = graph_case
    claim(c, "compact cross-task target", counters=[c["claim"]], support=[c["evidence"]])
    c["publish"]()
    requested = scope(c["claim"])
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], scope=requested)
    context = build_research_context(c["entry"], view="summary", scope=requested, max_bytes=65536)
    indexed = {item["node_id"]: item["ref"] for item in context["reference_index"]}
    expanded = [{"from_ref": indexed[e["from"]], "to_ref": indexed[e["to"]],
                 "kind": "refutes", "basis_ref": indexed[e["basis_node_id"]]}
                for e in context["relations"] if e["kind"] == "refutes"]
    assert {canonical(e) for e in expanded} == {canonical(e) for e in graph["scope_receipt"]["counter_relations"]}
    boundaries = [{"from": group["from"], "to": target, "relation": group["relation"]}
                  for group in context["scope_receipt"]["boundary_relation_groups"]
                  for target in group["to_node_ids"]]
    assert {canonical(e) for e in boundaries} == {canonical(e) for e in graph["scope_receipt"]["boundary_relations"]}
    assert len(boundaries) == context["scope_receipt"]["boundary_relation_count"]


def test_small_node_bound_cannot_drop_required_negative_set_and_claim_success(graph_case):
    c = graph_case
    negatives = [claim(c, f"required negative target {i}", counters=[c["claim"]]) for i in range(4)]
    c["publish"]()
    try:
        graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
                                     scope=scope(c["claim"]), max_nodes=1)
    except EvidenceError:
        return
    assert not graph["scope_receipt"]["counter_reference_coverage_complete"]
    assert not graph["scope_receipt"]["reference_coverage_complete"]
    assert graph["truncated"]


def test_ordinary_boundary_is_explicit_and_never_global_completion(graph_case):
    c = graph_case
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], scope=scope(c["claim"]))
    assert c["evidence"] in graph["scope_receipt"]["deliberate_boundary_refs"]
    assert graph["scope_receipt"]["global_reference_coverage_complete"] is False
    assert graph["scope_receipt"]["excerpt_coverage_complete"] is False


def test_scope_keeps_checkpoint_controls_and_replay_is_read_only(graph_case):
    c = graph_case
    watched = [c["store"].path, c["workspace"], c["entry"]]
    watched += [Path(c["journal"].get(r["record_id"])["path"])
                for r in (c["counter"], c["claim"], c["decision"], c["checkpoint"])]
    before = {str(p): file_identity(p) for p in watched}
    arguments = dict(view="node", node_id=node_identity(c["evidence"]),
                     scope=scope(c["evidence"]), max_bytes=32768)
    first = build_research_context(c["entry"], **arguments)
    second = build_research_context(c["entry"], **arguments)
    assert first == second
    refs = reachable_refs(first)
    assert c["counter"] in refs and c["decision"] in refs and c["claim"] in refs
    assert first["continuation"]["authority_state"] == "paused"
    assert first["continuation"]["pending"] == []
    assert first["execution_permission"] == "not_granted"
    assert first["automatic_retry"] is False
    assert {str(p): file_identity(p) for p in watched} == before
    assert not c["store"].actions()
    assert first["context_id"] == "research-context-" + digest({k: v for k, v in first.items() if k != "context_id"})
