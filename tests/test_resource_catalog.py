"""Inventory counterexamples; all inputs are fixtures, not scientific validation."""
import base64
import copy
import json

import pytest

from etalon import skills
from etalon.active.schema import canonical, digest
from etalon.research.entry import make_read_context
from etalon.research.evidence import StaleCursorError
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.meta_context import describe_resource, index_resources
from etalon.research.resource_catalog import build_resource_catalog
from test_evidence_query import publish, row
from test_evidence_scoped import source, watch


def part(name="declared-part"):
    return dict(part_id=name, conditions={"target": "fixture-SND1"}, requires=["target"],
                irrelevant=[], available_at="input", dependencies=[])


def artifact(tmp_path, number, content):
    path = tmp_path / f"artifact-{number}.json"
    path.write_text(canonical(content), encoding="utf-8")
    return {"record_id": f"artifact-{number}", "record_type": "artifact", "visibility": "released",
            "record": {"path": str(path), **file_identity(path), "source_kind": "fixture",
                       "role": "diagnostic", "bindings": {"schema": content["schema"]}}}


def collect(ws, **kwargs):
    pages, rows, cursor = [], [], None
    for _ in range(100):
        page = build_resource_catalog(ws, cursor=cursor, **kwargs)
        pages.append(page)
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return rows, pages
    pytest.fail("catalog did not finish within fixture bound")


def test_all_resource_kinds_preserve_declarations_without_activation(tmp_path):
    record = row(1)
    ref = make_ref("released", record["record_id"], record["record"])
    ws = publish(tmp_path, [record])
    kinds = ["method", "workflow", "claim", "evidence", "experience", "verifier", "template"]
    descriptors = [describe_resource(resource_id=kind, kind=kind, content_ref=ref,
        summary="Fixture " + kind, parts=[part()], counter_refs=[ref]) for kind in kinds]
    idx = index_resources(descriptors, workspace=ws)
    ws = publish(tmp_path, [record, artifact(tmp_path, "index", idx)])
    rows, pages = collect(ws, skill_refs=["research-index"], limit=2, scan_limit=1)
    assert set(r["kind"] for r in rows) == set(kinds) | {"skill"}
    declared = [r for r in rows if r["descriptor"] is not None]
    assert len(declared) == 7
    assert all(r["parts"] == [part()] and r["counter_refs"] == [ref] for r in declared)
    assert all(r["applicability"] == "declared_only" for r in declared)
    bare = next(r for r in rows if r["origin_ref"] == ref)
    assert bare["parts"] == [] and bare["applicability"] == "unknown"
    assert all(p["automatic_adoption"] is False and p["execution_permission"] == "not_granted" for p in pages)
    assert len({r["catalog_id"] for r in rows}) == len(rows)
    assert pages[-1]["coverage"]["discovery_complete"]


def test_empty_filtered_page_is_explicitly_incomplete_and_resume_finds_later_kind(tmp_path):
    method = row(2, schema="etalon-method-version/1", name="fixture", content_ref={"opaque": True})
    ws = publish(tmp_path, [row(1), method])
    first = build_resource_catalog(ws, kinds=["method"], scan_limit=1)
    assert first["items"] == [] and first["next_cursor"]
    assert first["coverage"]["status"] == "partial"
    second = build_resource_catalog(ws, kinds=["method"], scan_limit=1, cursor=first["next_cursor"])
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert second["items"][0]["applicability"] == "unknown"
    assert second["items"][0]["provenance"]["declarations"]["content_ref"] == {"opaque": True}


def test_research_never_exposes_evaluation_descriptor_summary(tmp_path):
    record = row(1)
    ref = make_ref("released", record["record_id"], record["record"])
    ws = publish(tmp_path, [record])
    descriptor = describe_resource(resource_id="hidden", kind="verifier", content_ref=ref,
        summary="PRIVATE_ANSWER_SENTINEL", parts=[part()], visibility="evaluation_only")
    ws = publish(tmp_path, [record, artifact(tmp_path, "hidden", descriptor)])
    research = build_resource_catalog(ws)
    assert "PRIVATE_ANSWER_SENTINEL" not in canonical(research)
    assert research["coverage"]["hidden_descriptors_this_page"] == 1
    evaluation = build_resource_catalog(ws, purpose="evaluation")
    assert any(r["resource_id"] == "hidden" for r in evaluation["items"])


def test_pinned_context_policy_cannot_be_upgraded_by_catalog_purpose(tmp_path):
    method = row(2, statement="POLICY_SECRET")
    ws = publish(tmp_path, [row(1), method])
    cp = make_ref("released", row(1)["record_id"], row(1)["record"])
    context = make_read_context(ws, cp, purpose="research", policy={"record_rules": [
        {"source_id": "released", "record_id": method["record_id"], "allowed_purposes": ["evaluation"]}]})
    for kwargs in ({"read_context": context}, {"entry": context}):
        result = build_resource_catalog(**kwargs)
        assert "POLICY_SECRET" not in canonical(result)
        with pytest.raises(EvidenceError, match="purpose"):
            build_resource_catalog(**kwargs, purpose="evaluation")


def test_scoped_sources_do_not_open_unrelated_broken_source(tmp_path, monkeypatch):
    ws = publish(tmp_path, [row(1)], extra_sources=[source(tmp_path, "other", [row(2)])])
    (tmp_path / "other.json").write_text("corrupt")
    visited = watch(monkeypatch)
    result = build_resource_catalog(ws, source_ids=["released"])
    assert "other" not in visited
    assert result["coverage"]["status"] == "complete"
    empty = build_resource_catalog(ws, source_ids=[])
    assert empty["items"] == [] and empty["coverage"]["source_ids"] == []
    with pytest.raises(EvidenceError, match="source_ids"):
        build_resource_catalog(ws, source_ids=["unknown"])


def test_missing_source_is_gap_not_complete_empty(tmp_path):
    missing = {"source_id": "missing", "kind": "journal", "location": "missing.sqlite",
               "identity": {"kind": "snapshot_sha256", "value": "0" * 64}}
    ws = publish(tmp_path, [], extra_sources=[missing])
    result = build_resource_catalog(ws)
    assert result["items"] == [] and result["next_cursor"] is None
    assert result["coverage"]["status"] == "partial"
    assert result["coverage"]["sources"][-1]["status"] == "unavailable"
    assert not (tmp_path / "missing.sqlite").exists()


def test_counter_original_mutation_cannot_leave_descriptor_verified(tmp_path):
    original = artifact(tmp_path, "original", {"schema": "fixture/1", "value": 1})
    ref = make_ref("released", original["record_id"], original["record"])
    ws = publish(tmp_path, [original])
    descriptor = describe_resource(resource_id="resource", kind="method", content_ref=ref,
        summary="Must disappear when counter is unavailable", parts=[part()], counter_refs=[ref])
    ws = publish(tmp_path, [original, artifact(tmp_path, "descriptor", descriptor)])
    assert any(r["descriptor"] for r in build_resource_catalog(ws)["items"])
    (tmp_path / "artifact-original.json").write_text("corrupt")
    result = build_resource_catalog(ws)
    assert result["items"] == []
    assert result["coverage"]["status"] == "partial"
    assert {g["code"] for g in result["coverage"]["gaps"]} == {"resource_unavailable", "descriptor_unavailable"}


def test_cursor_binds_scope_source_and_selected_skill_version(tmp_path):
    ws = publish(tmp_path, [row(1), row(2)])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "SKILL.md").write_text("---\nname: fixture\ndescription: Fixture bundle\n---\nFixture\n")
    (bundle / "helper.py").write_text("# fixture")
    selected = [{"slug": "fixture", "bundle_ref": {"root": str(bundle)}}]
    first = build_resource_catalog(ws, skill_refs=selected, limit=1)
    with pytest.raises(StaleCursorError):
        build_resource_catalog(ws, skill_refs=selected, kinds=["method"], cursor=first["next_cursor"])
    (bundle / "helper.py").write_text("# changed")
    with pytest.raises(StaleCursorError):
        build_resource_catalog(ws, skill_refs=selected, cursor=first["next_cursor"])
    pinned = copy.deepcopy(selected)
    pinned[0]["manifest_sha256"] = "0" * 64
    with pytest.raises(EvidenceError, match="changed|digest"):
        build_resource_catalog(ws, skill_refs=pinned)


def test_exact_skill_members_have_unknown_applicability(tmp_path):
    ws = publish(tmp_path, [])
    result = build_resource_catalog(ws, source_ids=[], skill_refs=["research-index"])
    manifest, _ = skills.snapshot("research-index")
    skill = result["items"][0]
    assert skill["content_ref"]["manifest_sha256"] == manifest["manifest_sha256"]
    assert [p["part_id"] for p in skill["parts"]] == [p["path"] for p in manifest["files"]]
    assert all(p["conditions"] is None and p["applicability"] == "unknown" for p in skill["parts"])
    assert skill["descriptor"] is None


def test_hidden_cross_source_counter_cannot_leak_resource(tmp_path):
    secret = row(9, statement="HIDDEN_COUNTER")
    secret_ref = make_ref("secret", secret["record_id"], secret["record"])
    record = row(1)
    ref = make_ref("released", record["record_id"], record["record"])
    descriptor = describe_resource(resource_id="derived", kind="claim", content_ref=ref,
        summary="DERIVED_SECRET", parts=[part()], counter_refs=[secret_ref])
    ws = publish(tmp_path, [record, artifact(tmp_path, "descriptor", descriptor)],
                 extra_sources=[source(tmp_path, "secret", [secret], visibility="sealed")])
    result = build_resource_catalog(ws, source_ids=["released"])
    assert "DERIVED_SECRET" not in canonical(result) and "HIDDEN_COUNTER" not in canonical(result)
    assert result["coverage"]["gaps"]


def test_bounds_and_bad_cursor_are_errors(tmp_path):
    ws = publish(tmp_path, [row(1)])
    for kwargs in ({"limit": 51}, {"scan_limit": 0}, {"cursor": "bad"}, {"kinds": ["unsupported"]},
                   {"skill_refs": ["unknown-skill"]}, {"max_bytes": 4095}, {"max_bytes": 65537}):
        with pytest.raises(EvidenceError):
            build_resource_catalog(ws, **kwargs)


def test_byte_pagination_preserves_every_row_and_counter_pointer(tmp_path):
    ws = publish(tmp_path, [row(n, statement="Fixture " + "x" * 300) for n in range(4)])
    rows, pages = collect(ws, max_bytes=4096, limit=50)
    assert len(rows) == 4 and len(pages) > 1
    assert all(len(canonical(p).encode("utf-8")) <= 4096 for p in pages)
    assert len({r["catalog_id"] for r in rows}) == 4


def test_prior_failure_remains_partial_on_last_page(tmp_path):
    broken = artifact(tmp_path, "0", {"schema": "fixture/1"})
    ws = publish(tmp_path, [broken, row(1), row(2)])
    (tmp_path / "artifact-0.json").write_text("bad")
    rows, pages = collect(ws, limit=1)
    assert len(rows) == 2 and len(pages) == 2
    assert pages[-1]["next_cursor"] is None
    assert pages[-1]["coverage"]["status"] == "partial"
    assert pages[-1]["coverage"]["gap_observations_prior_pages"] == 1


def test_changed_descriptor_between_partial_pages_rejects_cursor(tmp_path):
    record = row(1)
    ref = make_ref("released", record["record_id"], record["record"])
    ws = publish(tmp_path, [record])
    resources = [describe_resource(resource_id=str(n), kind="method", content_ref=ref,
        summary="fixture", parts=[part()]) for n in range(3)]
    indexed = artifact(tmp_path, "index", index_resources(resources, workspace=ws))
    ws = publish(tmp_path, [record, indexed])
    first = build_resource_catalog(ws, limit=1)
    (tmp_path / "artifact-index.json").write_text("corrupt")
    with pytest.raises(StaleCursorError):
        build_resource_catalog(ws, limit=1, cursor=first["next_cursor"])


def test_invalid_context_is_rejected_before_reading_its_purpose():
    with pytest.raises(EvidenceError, match="invalid_read_context"):
        build_resource_catalog(read_context={})


def test_malformed_cursor_payload_and_out_of_range_skill_are_rejected(tmp_path):
    ws = publish(tmp_path, [row(1), row(2)])
    with pytest.raises(EvidenceError, match="invalid catalog cursor"):
        build_resource_catalog(ws, cursor=base64.urlsafe_b64encode(b'"scalar"').decode())
    first = build_resource_catalog(ws, limit=1)
    state = json.loads(base64.urlsafe_b64decode(first["next_cursor"]))
    state.update(phase="skills", skill_index=1)
    with pytest.raises(EvidenceError, match="outside the selected library"):
        build_resource_catalog(ws, cursor=base64.urlsafe_b64encode(canonical(state).encode()).decode())


def test_counter_validation_is_not_limited_to_query_summary_refs(tmp_path):
    records = [row(n) for n in range(12)]
    last = artifact(tmp_path, "z-last-counter", {"schema": "fixture/1"})
    refs = [make_ref("released", r["record_id"], r["record"]) for r in [*records, last]]
    ws = publish(tmp_path, [*records, last])
    descriptor = describe_resource(resource_id="method", kind="method", content_ref=refs[0],
        summary="fixture", parts=[part()], counter_refs=refs)
    ws = publish(tmp_path, [*records, last, artifact(tmp_path, "a-descriptor", descriptor)])
    first = build_resource_catalog(ws, kinds=["method"])
    assert first["items"][0]["counter_refs"] == refs
    (tmp_path / "artifact-z-last-counter.json").write_text("bad")
    changed = build_resource_catalog(ws, kinds=["method"])
    assert changed["items"] == []
    assert any(g["code"] == "descriptor_unavailable" for g in changed["coverage"]["gaps"])
