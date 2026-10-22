"""Source access and visibility counterexamples; all data is marked fixture."""
import json

import pytest

from etalon.active.schema import digest
from etalon.research import evidence
from etalon.research.evidence_refs import file_identity, make_ref
from test_evidence_query import publish, row


def source(tmp_path, name, rows, **extra):
    path = tmp_path / (name + ".json")
    path.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": rows}))
    return {"source_id": name, "kind": "artifact_manifest", "location": path.name,
            "identity": {"kind": "snapshot_sha256", "value": file_identity(path)["sha256"]}, **extra}


def watch(monkeypatch):
    visited = []
    original = evidence._source
    def read(ws, selected):
        visited.append(selected["source_id"])
        return original(ws, selected)
    monkeypatch.setattr(evidence, "_source", read)
    return visited


def test_source_filter_and_direct_ref_never_open_unrelated_source(tmp_path, monkeypatch):
    unused = source(tmp_path, "unrelated", [row(9)])
    ws = publish(tmp_path, [row(1), row(2)], extra_sources=[unused])
    (tmp_path / "unrelated.json").write_text("corrupt")
    visited = watch(monkeypatch)
    page = evidence.query_evidence(ws, filters={"source_ids": ["released"]})
    assert visited == ["released"]
    assert [c["source_id"] for c in page["coverage"]] == ["released"]
    visited.clear()
    assert evidence.read_evidence(ws, page["items"][0]["ref"])["availability"] == "available"
    assert visited == ["released"]
    visited.clear()
    assert evidence.query_evidence(ws, filters={"source_ids": []})["items"] == []
    assert visited == []


def test_scoped_query_follows_exact_cross_source_dependencies_only(tmp_path, monkeypatch):
    original = row(4)
    ref = make_ref("dependency", original["record_id"], original["record"])
    dependency = source(tmp_path, "dependency", [original])
    unrelated = source(tmp_path, "unrelated", [row(5)])
    ws = publish(tmp_path, [row(1, evidence_refs=[ref])], extra_sources=[unrelated, dependency])
    visited = watch(monkeypatch)
    page = evidence.query_evidence(ws, filters={"source_ids": ["released"]})
    assert visited == ["released", "dependency"]
    assert len(page["items"]) == 1
    assert page["coverage"][1]["scope"] == "dependency"
    assert evidence.read_evidence(ws, page["items"][0]["ref"], view="lineage")["items"] == [ref]


def test_unrelated_manifest_edit_keeps_scoped_cursor_but_dependency_change_invalidates(tmp_path):
    target = row(8)
    ref = make_ref("dep", target["record_id"], target["record"])
    ws = publish(tmp_path, [row(n, evidence_refs=[ref]) for n in range(3)],
                 extra_sources=[source(tmp_path, "dep", [target]), source(tmp_path, "other", [row(9)])])
    first = evidence.query_evidence(ws, filters={"source_ids": ["released"]}, limit=1)
    config = json.loads(ws.read_text())
    config["sources"][2]["identity"]["value"] = digest("unrelated edit")
    ws.write_text(json.dumps(config))
    assert evidence.query_evidence(ws, filters={"source_ids": ["released"]}, limit=1, cursor=first["next_cursor"])["items"]
    config["sources"][1]["identity"]["value"] = digest("dependency changed")
    ws.write_text(json.dumps(config))
    with pytest.raises(evidence.StaleCursorError):
        evidence.query_evidence(ws, filters={"source_ids": ["released"]}, limit=1, cursor=first["next_cursor"])


def test_scoping_cannot_bypass_transitive_sealed_visibility(tmp_path, monkeypatch):
    secret = row(9, statement="SECRET")
    hidden = source(tmp_path, "hidden", [secret], visibility="sealed")
    secret_ref = make_ref("hidden", secret["record_id"], secret["record"])
    derived = row(8, statement="SECRET derivative", evidence_refs=[secret_ref])
    derivative_source = source(tmp_path, "dep", [derived])
    ref = make_ref("dep", derived["record_id"], derived["record"])
    ws = publish(tmp_path, [row(1, statement="SECRET second derivative", evidence_refs=[ref]), row(2)],
                 extra_sources=[derivative_source, hidden])
    visited = watch(monkeypatch)
    page = evidence.query_evidence(ws, filters={"source_ids": ["released"]})
    assert visited == ["released", "dep"]
    assert len(page["items"]) == 1 and "SECRET" not in json.dumps(page)
    assert page["coverage"][-1]["reason"] == "source_not_published"


def test_dependency_bound_reports_unavailable_not_successful_empty(tmp_path, monkeypatch):
    targets = [row(2), row(3)]
    refs = [make_ref(name, target["record_id"], target["record"]) for name, target in zip(("a", "b"), targets)]
    ws = publish(tmp_path, [row(1, evidence_refs=refs)], extra_sources=[source(tmp_path, name, [target])
                 for name, target in zip(("a", "b"), targets)])
    monkeypatch.setattr(evidence, "MAX_DEPENDENCY_SOURCES", 1)
    page = evidence.query_evidence(ws, filters={"source_ids": ["released"]})
    assert page["items"] == []
    assert page["coverage"][0]["status"] == "unavailable"
    assert "bound exceeded" in page["coverage"][0]["reason"]
