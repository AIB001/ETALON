"""Branch navigation counterexamples; synthetic records are not science runs."""
from pathlib import Path

import pytest

from etalon.active.schema import canonical
from etalon.research.branch_context import build_branch_context
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.improvement import _local
from etalon.research.meta_context import WORKSET, _dependencies, build_task_workset
from etalon.research.method_revision import journal_ref
from test_research_continuation import case, checkpoint, publish, save, step
from test_research_graph_memory import graph_case
from test_resource_usage import artifact, chain, record


def entry_for_chain(case, payload, *, activate=False):
    refs = [payload[k] for k in ("node_ref", "evaluation_ref", "selection_ref")]
    if activate:
        value = _local(case[0], payload["workset_ref"], "research")
        wr = artifact(case, value, dict(schema=WORKSET, meta_activation=WORKSET,
            task_id="case", evidence_refs=_dependencies(value)))
        refs.append(save(case, step(case[1], output_refs=[wr])))
    branches = [dict(branch_id=b, state="resolved", basis_refs=[case[1]], next_read_refs=[],
                     reconsider_when="New evidence") for b in ("available", "existing", "other")]
    cp = save(case, checkpoint(case, refs[-1], step_refs=refs, branches=branches))
    return dict(workspace=publish(case), checkpoint_ref=cp)


def test_exact_branch_preserves_pause_counters_and_no_writes(graph_case):
    c = graph_case
    before = file_identity(c["store"].path)
    result = build_branch_context(c["entry"], branch_id="fixed-core", limit=64)
    assert result["branch"]["branch_id"] == "fixed-core"
    assert result["continuation"]["authority_state"] == "paused"
    assert any(i["item_type"] == "decision" and i["ref"] == c["decision"] for i in result["items"])
    assert any(e["kind"] == "refutes" for e in result["relations"])
    assert all(e["verification"].startswith("declared_") for e in result["relations"])
    assert result["task_workset"]["status"] == "legacy_unscoped"
    assert not result["automatic_activation"] and result["execution_permission"] == "not_granted"
    assert file_identity(c["store"].path) == before and not c["store"].actions()
    with pytest.raises(EvidenceError, match="not present"):
        build_branch_context(c["entry"], branch_id="fixed")


def test_verified_usage_is_branch_specific_but_not_historical_consumption(case):
    payload = chain(case)
    ref = journal_ref(case[0], record(case, payload), source_id="research")
    entry = entry_for_chain(case, payload)
    result = build_branch_context(entry, branch_id="available", limit=64, max_bytes=65536)
    usage = next(i for i in result["items"] if i["item_type"] == "resource_usage")
    assert usage["ref"] == ref and usage["chain_complete"]
    assert usage["selected"] and usage["part_attribution"] == "explicit"
    assert [a["relation"] for a in usage["branch_associations"]] == ["node_ref"]
    assert all(o["status"] == "unknown" for o in usage["observations"].values())
    assert usage["causal_benefit"] == "not_established"
    assert any("method_use" in i for i in result["items"])
    other = build_branch_context(entry, branch_id="other", max_bytes=65536)
    assert not any(i["item_type"] == "resource_usage" for i in other["items"])
    assert other["coverage"]["usage_discovery"]["unassigned_to_selected_branch_count"] == 1


def test_task_workset_does_not_turn_into_branch_activation(case):
    payload = chain(case)
    entry = entry_for_chain(case, payload, activate=True)
    before = file_identity(case[0].store.path)
    first = build_branch_context(entry, branch_id="available", conditions={"target": "SND1"}, max_bytes=65536)
    second = build_branch_context(entry, branch_id="other", conditions={"target": "PoseBusters"}, max_bytes=65536)
    assert first["task_workset"]["status"] == "active"
    assert second["task_workset"]["status"] == "review_required"
    assert second["task_workset"]["ref"] == first["task_workset"]["ref"]
    assert second["task_workset"]["branch_activation"] == "not_inferred"
    parts = [i for i in second["items"] if i["item_type"] == "resource_part"]
    assert parts and all(p["branch_conditions_review_required"] for p in parts)
    assert all(p["selection_scope"] == "task_wide_not_branch_activation" for p in parts)
    assert file_identity(case[0].store.path) == before


def test_template_fallback_keeps_excluded_method(case):
    payload = chain(case, actual_target="PoseBusters")
    original = _local(case[0], payload["workset_ref"], "research")
    fallback = build_task_workset(original["profile"], original["index"], original["review"],
        template=original["template"], mode="template", fallback_reason="SND1 parameters are incompatible")
    wr = artifact(case, fallback, dict(schema=WORKSET, meta_activation=WORKSET, task_id="case",
                                       evidence_refs=_dependencies(fallback)))
    activation = save(case, step(case[1], output_refs=[wr]))
    cp = save(case, checkpoint(case, activation))
    result = build_branch_context(dict(workspace=publish(case), checkpoint_ref=cp),
                                  branch_id="existing", max_bytes=65536)
    assert result["task_workset"]["mode"] == "template"
    part = next(i for i in result["items"] if i.get("resource_id") == "method")
    assert part["decision"]["disposition"] == "exclude" and not part["selected"]
    assert part["declared_applicability"] == "conflict"
    assert set(part["historical_phases"].values()) == {"unknown"}


def test_experience_matches_remain_declarations_and_mismatch_is_preserved(case):
    payload = chain(case)
    content = dict(schema="etalon-method-experience/1", task_id="case", scope="fixture",
        conditions={"target": "SND1"}, method_refs=[payload["method_ref"]], supersedes=[],
        patch_ref=None, evaluation_ref=None, resolution_ref=None, outcome="unresolved",
        lesson="Keep identity checks, reject setting transfer", invalid_when="Different protocol",
        support_refs=[case[1]], counter_refs=[], capability_id="molquarry.data")
    er = artifact(case, content, dict(schema=content["schema"], task_id="case"))
    entry = entry_for_chain(case, payload)
    for conditions, category in ((None, "reference"), ({"target": "SND1"}, "applicable"),
                                  ({"target": "PoseBusters"}, "excluded")):
        result = build_branch_context(entry, branch_id="available", conditions=conditions, max_bytes=65536)
        experience = next(i for i in result["items"] if i["item_type"] == "experience" and i["ref"] == er)
        assert experience["category"] == category
        assert experience["scientific_applicability"] == "not_verified"
        assert experience["experience"]["invalid_when"] == "Different protocol"


def test_cursor_is_bound_to_scope_and_each_page_is_bounded(graph_case):
    c = graph_case
    page = build_branch_context(c["entry"], branch_id="fixed-core", limit=1, max_bytes=8192)
    assert page["next_cursor"] and len(canonical(page).encode()) <= 8192
    cursor = page["next_cursor"]
    with pytest.raises(EvidenceError, match="stale"):
        build_branch_context(c["entry"], branch_id="fixed-core", cursor=cursor,
                             conditions={"target": "other"}, max_bytes=8192)
    seen = page["items"] + page["relations"]
    while cursor:
        page = build_branch_context(page["read_context"], branch_id="fixed-core", cursor=cursor,
                                    limit=1, max_bytes=8192)
        seen.extend(page["items"] + page["relations"])
        assert len(canonical(page).encode()) <= 8192
        cursor = page["next_cursor"]
    assert len(seen) == page["selection"]["candidate_count"]
    assert len({canonical(i) for i in seen}) == len(seen)


def test_forged_and_cross_task_usage_fail_closed(case):
    payload = chain(case)
    ref = journal_ref(case[0], record(case, payload), source_id="research")
    entry = entry_for_chain(case, payload)
    with pytest.raises(EvidenceError):
        build_branch_context(entry, branch_id="available", usage_refs=[{**ref, "record_digest": "0" * 64}])
    alternate = save(case, step(case[1], task_id="different"))
    cp = save(case, checkpoint(case, alternate, task_id="different"))
    with pytest.raises(EvidenceError, match="another task"):
        build_branch_context(dict(workspace=publish(case), checkpoint_ref=cp), branch_id="existing", usage_refs=[ref])


def test_invalid_discovered_usage_is_a_gap_not_a_verified_join(case):
    payload = chain(case)
    from etalon.research.resource_usage import SCHEMA
    invalid = artifact(case, payload, dict(schema=SCHEMA, task_id="case"))
    entry = entry_for_chain(case, payload)
    result = build_branch_context(entry, branch_id="available", max_bytes=65536)
    assert result["coverage"]["usage_discovery"]["unverified_count"] == 1
    assert not result["coverage"]["complete"]
    assert any(i.get("ref") == invalid for i in result["source_issues"])
    assert not any(i["item_type"] == "resource_usage" for i in result["items"])
    with pytest.raises(EvidenceError):
        build_branch_context(entry, branch_id="available", usage_refs=[invalid])


def test_restricted_or_missing_sources_cannot_look_complete(graph_case):
    c = graph_case
    with pytest.raises(EvidenceError, match="outside permitted"):
        build_branch_context(c["entry"], branch_id="fixed-core", policy={"source_ids": []})
    Path(c["journal"].get(c["counter"]["record_id"])["path"]).write_text("tampered")
    with pytest.raises(EvidenceError):
        build_branch_context(c["entry"], branch_id="fixed-core")


def test_graph_truncation_and_discovery_pagination_report_incomplete(graph_case, monkeypatch):
    from etalon.research import branch_context
    original = branch_context.query_evidence
    def partial(*args, **kwargs):
        page = original(*args, **kwargs)
        page["next_cursor"] = "synthetic-test-pagination-token"
        return page
    monkeypatch.setattr(branch_context, "query_evidence", partial)
    result = build_branch_context(graph_case["entry"], branch_id="fixed-core", max_nodes=1)
    assert result["truncated"] and not result["coverage"]["complete"]
    assert result["coverage"]["graph_truncated"]
    assert result["coverage"]["usage_discovery"]["next_cursor"]


def test_fifty_one_real_usage_records_expose_discovery_tail(case):
    from etalon.research.resource_usage import SCHEMA, _links, usage_dependencies
    payload = chain(case)
    for index in range(51):
        value = {**payload, "provenance": {**payload["provenance"], "note": f"Synthetic declaration {index}"}}
        artifact(case, value, dict(schema=SCHEMA, task_id="case",
            evidence_refs=usage_dependencies(value), resource_usage_links=_links(value)))
    entry = entry_for_chain(case, payload)
    result = build_branch_context(entry, branch_id="available", limit=1, max_bytes=16384)
    discovery = result["coverage"]["usage_discovery"]
    # Evidence query's own byte budget may yield fewer than its row limit.
    assert 1 <= discovery["returned_count"] <= 50 and discovery["next_cursor"]
    assert not result["coverage"]["complete"] and result["truncated"]
    from etalon.research.evidence import query_evidence
    count, cursor = discovery["returned_count"], discovery["next_cursor"]
    while cursor:
        tail = query_evidence(entry["workspace"], query=SCHEMA, filters={"record_types": ["artifact"]},
                             cursor=cursor, limit=50)
        count += len(tail["items"])
        cursor = tail["next_cursor"]
    assert count == 51
