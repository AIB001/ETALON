"""Lossless optional aliases do not merge source roles or bypass verification."""
from pathlib import Path

import pytest

from etalon.active.schema import canonical
from etalon.research import Artifact
from etalon.research.decision_reading import build_decision_delivery
from etalon.research.evidence import StaleCursorError
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from test_decision_delivery import delivered
from test_decision_reading_mcp import reader
from test_research_graph_memory import graph_case


def duplicate_case(c, raw):
    refs = []
    for name in ("support-copy", "counter-copy"):
        path = c["entry"].parent / name
        path.write_bytes(raw)
        artifact = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic", bindings={})
        c["journal"].record(artifact)
        refs.append(make_ref("science", artifact.id, c["journal"].get(artifact.id)))
    claim = c["save"]({"schema": "etalon-research-claim/1", "question": "Same bytes, distinct provenance?",
        "statement": "Synthetic alias contract; no scientific result", "epistemic_status": "interpretation",
        "subjects": ["fixture"], "conditions": [], "limitations": [], "support_refs": [refs[0]],
        "counter_refs": [refs[1]], "method_refs": [], "supersedes": []})
    c["publish"]()
    return {"root_refs": [claim], "ordinary_hops": 0}, refs


@pytest.mark.parametrize("raw", [("完整原文与反证。" * 800).encode(), bytes(range(256)) * 70], ids=["utf8", "binary"])
def test_aliases_reconstruct_each_exact_body_and_preserve_counter_direction(graph_case, raw):
    c = graph_case
    scope, refs = duplicate_case(c, raw)
    old, old_bodies, old_meta = delivered(c["entry"], scope=scope, max_bytes=4096)
    pages, bodies, meta = delivered(c["entry"], scope=scope, max_bytes=4096, deduplicate_bodies=True)
    aliases = [row for row in meta if row["section"] == "original_alias"]
    assert len(aliases) == 1
    for row in aliases:
        source = canonical(row["body_alias"]["ref"])
        assert source in bodies
        assert row["body_alias"]["delivery"] == "earlier_original_in_same_delivery"
        assert row["verification"]["artifact"] == "verified"
        bodies[canonical(row["ref"])] = bodies[source]
    assert bodies == old_bodies
    assert all(bodies[canonical(ref)] == raw for ref in refs)
    assert [r for r in meta if r["section"] == "counter_relation"] == [
        r for r in old_meta if r["section"] == "counter_relation"]
    assert pages[0]["required_original_count"] == old[0]["required_original_count"]
    assert pages[0]["counterevidence"] == old[0]["counterevidence"]
    assert sum(len(canonical(p).encode()) for p in pages) < sum(len(canonical(p).encode()) for p in old)


def test_conditional_alias_does_not_claim_a_delivered_or_understood_body(graph_case):
    scope, _ = duplicate_case(graph_case, b"same optional body")
    pages, bodies, meta = delivered(graph_case["entry"], scope=scope, reading_mode="status", deduplicate_bodies=True)
    assert not bodies and pages[0]["required_original_count"] == 0
    aliases = [r for r in meta if "body_alias" in r]
    assert aliases
    assert all(r["section"] == "conditional_original" for r in aliases)
    assert all(r["body_alias"]["delivery"] == "conditional_original_not_delivered" for r in aliases)
    assert all(r["body_delivery"].startswith("not_requested") for r in aliases)
    assert all(r["verification"]["artifact"] == "not_checked" for r in aliases)
    assert pages[0]["counterevidence"]["original_bodies_read_by_host"] == "not_established"


def test_missing_optional_boundary_alias_adds_no_original_read_gate(graph_case):
    c = graph_case
    _, refs = duplicate_case(c, b"optional identical bytes")
    claim = c["save"]({"schema": "etalon-research-claim/1", "question": "Status only?",
        "statement": "Optional boundary fixture", "epistemic_status": "interpretation", "subjects": ["fixture"],
        "conditions": [], "limitations": [], "support_refs": refs, "counter_refs": [],
        "method_refs": [], "supersedes": []})
    c["publish"]()
    Path(c["journal"].get(refs[1]["record_id"])["path"]).unlink()
    options = dict(scope={"root_refs": [claim], "ordinary_hops": 0}, reading_mode="status")
    ordinary, _, _ = delivered(c["entry"], **options)
    pages, bodies, metadata = delivered(c["entry"], **options, deduplicate_bodies=True)
    aliases = [row for row in metadata if "body_alias" in row]
    assert aliases and not bodies
    assert pages[0]["counterevidence"] == ordinary[0]["counterevidence"]
    assert all(row["section"] == "conditional_original" for row in aliases)
    assert all(row["verification"] == {"record": "verified", "artifact": "not_checked"} for row in aliases)
    assert all("matching_declared_identity_only" in row["body_alias"]["meaning"] for row in aliases)


def test_alias_source_tampering_and_option_changes_fail_closed(graph_case):
    c = graph_case
    scope, refs = duplicate_case(c, b"shared original")
    args = dict(question="choose", scope=scope, limit=1, deduplicate_bodies=True)
    first = build_decision_delivery(c["entry"], **args)
    with pytest.raises(StaleCursorError):
        build_decision_delivery(first["read_context"], **{**args, "deduplicate_bodies": False}, cursor=first["next_cursor"])
    Path(c["journal"].get(refs[1]["record_id"])["path"]).write_bytes(b"same declared hash, changed original")
    with pytest.raises(EvidenceError):
        build_decision_delivery(first["read_context"], **args, cursor=first["next_cursor"])


def test_mcp_option_is_delivery_only_and_strict_boolean(graph_case):
    import json
    read = reader()
    args = dict(entry=str(graph_case["entry"]), question="choose", scope={"root_refs": [graph_case["claim"]]})
    result = json.loads(read(**args, view="delivery", view_options={"deduplicate_bodies": True}))
    assert result["ok"] and "body_aliases" in result
    assert not json.loads(read(**args, view="reading", view_options={"deduplicate_bodies": True}))["ok"]
    assert not json.loads(read(**args, view="delivery", view_options={"deduplicate_bodies": "true"}))["ok"]
