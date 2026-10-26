"""Optional readable delivery reuses exact selection and original verification."""
import base64
import json
from pathlib import Path

import pytest

from etalon.active.schema import canonical
from etalon.research import Artifact
from etalon.research.decision_reading import build_decision_delivery, build_decision_reading
from etalon.research.evidence import read_evidence, StaleCursorError
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from test_research_graph_memory import graph_case
from test_decision_reading import pages as reading_pages
from test_decision_reading_mcp import reader


def delivered(entry, **options):
    pages, bodies, metadata, fragments = [], {}, [], {}
    args = dict(entry=entry, question="Which exact decision can change?", **options)
    while True:
        page = build_decision_delivery(**args)
        assert len(canonical(page).encode()) <= options.get("max_bytes", 16384)
        pages.append(page)
        for item in page["items"]:
            if item.get("section") == "original":
                fragment = item["fragment"]
                data = bodies.setdefault(canonical(item["ref"]), bytearray())
                assert len(data) == fragment["offset"]
                data.extend(fragment["content"].encode() if fragment["encoding"] == "utf-8"
                            else base64.b64decode(fragment["content"]))
                if fragment["next_offset"] is None:
                    assert len(data) == fragment["total_bytes"]
            elif "value" in item:
                metadata.append(item["value"])
            else:
                fragment = item["fragment"]
                data = fragments.setdefault(item["index"], bytearray())
                assert len(data) == fragment["offset"]
                data.extend(base64.b64decode(fragment["content"]))
                if fragment["next_offset"] is None:
                    metadata.append(json.loads(data))
        if page["next_cursor"] is None:
            return pages, bodies, metadata
        args.update(entry=page["read_context"], cursor=page["next_cursor"])
        assert len(pages) < 150


def test_selected_originals_and_counter_bodies_equal_existing_reader(graph_case):
    c = graph_case
    before = file_identity(c["store"].path)
    scope = {"root_refs": [c["decision"]], "ordinary_hops": 0}
    rows, old = reading_pages(c["entry"], scope=scope)
    pages, bodies, meta = delivered(c["entry"], scope=scope)
    required = [r for r in rows if r.get("read_requirement") == "required_before_changed_decision_or_causal_claim"]
    assert set(bodies) == {canonical(r["ref"]) for r in required}
    assert canonical(c["counter"]) in bodies
    assert "PRIVATE_SENTINEL" not in canonical(pages)
    for row in required:
        original = read_evidence(read_context=old[0]["read_context"], ref=row["ref"], **row["original_read"])
        raw = (base64.b64decode(original["fragment"]["content"]) if "fragment" in original
               else canonical(original["record"]).encode())
        assert bodies[canonical(row["ref"])] == raw
    assert any(r.get("from_ref") == c["counter"] and r["to_ref"] == c["claim"] for r in meta)
    assert any(r["section"] == "conditional_original" for r in meta)
    assert pages[0]["continuation"]["authority_state"] == "paused"
    assert all(p["counterevidence"]["original_bodies_read_by_host"] == "not_established" for p in pages)
    assert file_identity(c["store"].path) == before and not c["store"].actions()


def test_pages_preserve_utf8_fragments_and_do_not_repeat_metadata(graph_case):
    c = graph_case
    text = "有条件的反证与未知。" * 300
    path = c["entry"].parent / "utf8.txt"
    path.write_text(text, encoding="utf-8")
    artifact = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic", bindings={})
    c["journal"].record(artifact)
    blob = make_ref("science", artifact.id, c["journal"].get(artifact.id))
    large = c["save"]({"schema": "etalon-research-claim/1", "question": "fixture?", "statement": "UTF-8 fixture",
        "epistemic_status": "interpretation", "subjects": ["fixture"], "conditions": [],
        "limitations": [], "support_refs": [blob], "counter_refs": [c["counter"]],
        "method_refs": [], "supersedes": []})
    c["publish"]()
    pages, bodies, meta = delivered(c["entry"], scope={"root_refs": [large], "ordinary_hops": 0}, max_bytes=4096)
    assert len(pages) > 3
    assert bodies[canonical(blob)].decode("utf-8") == text
    assert sum(r["section"] == "selection" for r in meta) == 1
    assert any(r["section"] == "gaps" for r in meta)
    assert all(p["execution_permission"] == "not_granted" and not p["automatic_retry"] for p in pages)


def test_original_corruption_and_scope_changes_cannot_reuse_delivery_cursor(graph_case):
    c = graph_case
    options = dict(question="choose", scope={"root_refs": [c["decision"]], "ordinary_hops": 0}, limit=1)
    first = build_decision_delivery(c["entry"], **options)
    with pytest.raises(StaleCursorError):
        build_decision_delivery(c["entry"], **{**options, "question": "different"}, cursor=first["next_cursor"])
    Path(c["journal"].get(c["counter"]["record_id"])["path"]).write_text("changed counter")
    with pytest.raises(EvidenceError):
        build_decision_delivery(first["read_context"], **options, cursor=first["next_cursor"])


def test_status_delivery_does_not_claim_or_read_unrequested_bodies(graph_case):
    pages, bodies, meta = delivered(graph_case["entry"], reading_mode="status")
    assert not bodies
    assert pages[0]["required_original_count"] == 0
    assert any(row["section"] == "conditional_original" for row in meta)


def test_delivery_uses_existing_mcp_and_same_bounded_options(graph_case):
    read = reader()
    args = dict(entry=str(graph_case["entry"]), view="delivery", question="choose",
                scope={"root_refs": [graph_case["claim"]], "ordinary_hops": 0}, max_bytes=4096)
    pages = []
    while True:
        text = read(**args)
        assert len(text.encode()) <= 4096
        page = json.loads(text)
        assert page["ok"], page
        pages.append(page)
        if page["next_cursor"] is None:
            break
        args.update(entry=page["read_context"], cursor=page["next_cursor"])
        assert len(pages) < 100
    assert any(i.get("section") == "original" and i["ref"] == graph_case["counter"]
               for page in pages for i in page["items"])
    assert not json.loads(read(**{**args, "max_bytes": 32768}))["ok"]
