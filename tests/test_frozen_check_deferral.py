"""Defer redundant checks, never the outer original verification obligation."""
import copy
import json
import sqlite3
from pathlib import Path

import pytest

from etalon.research import continuation, evidence, evidence_refs, read_scope
from etalon.research.evidence_refs import EvidenceError, load_workspace
from etalon.research.read_scope import bounded_read, verified_read
from etalon.research.research_context import build_research_context
from test_research_graph_memory import graph_case
from test_verified_read_scope import _same_size_change


def _hash_calls(monkeypatch):
    calls = []
    original = evidence_refs.file_identity
    def measured(path):
        calls.append(str(Path(path).absolute()))
        return original(path)
    for module in (evidence_refs, continuation, evidence):
        monkeypatch.setattr(module, "file_identity", measured)
    return calls


def test_deferred_checks_reduce_calls_but_keep_final_original_verification_set(graph_case, monkeypatch):
    c = graph_case
    calls = _hash_calls(monkeypatch)
    original = read_scope.ReadScope.defer_frozen_check
    runs = []
    for defer in (False, True):
        monkeypatch.setattr(read_scope.ReadScope, "defer_frozen_check", original if defer else lambda *a: False)
        calls.clear()
        with verified_read() as scope:
            ws = load_workspace(c["workspace"])
            records, coverage, _ = evidence._collect(ws)
            before_nested = len(calls)
            continuation._check_frozen_view(ws, records, coverage)
            continuation._check_frozen_view(ws, records, coverage)
            after_nested = len(calls)
            if defer:
                assert before_nested == after_nested
            before_final = len(calls)
        final_paths = set(calls[before_final:])
        assert str(c["store"].path.absolute()) in final_paths
        assert str(Path(c["journal"].get(c["claim"]["record_id"])["path"]).absolute()) in final_paths
        runs.append((len(calls), set(calls), final_paths, scope.stats()))
    assert runs[1][0] < runs[0][0]
    assert runs[0][1] == runs[1][1] and runs[0][2] == runs[1][2]
    assert runs[1][3]["deferred_frozen_checks"] == 2


@pytest.mark.parametrize("damage", ["source", "projection", "workspace"])
def test_change_after_internal_read_prevents_outer_result_return(graph_case, damage):
    c = graph_case
    path = (c["store"].path if damage == "source" else c["workspace"] if damage == "workspace"
            else Path(c["journal"].get(c["claim"]["record_id"])["path"]))
    returned = []
    @bounded_read
    def read_and_tamper():
        result = build_research_context(c["entry"], view="card")
        assert read_scope.CURRENT.get().deferred_frozen_checks > 0
        _same_size_change(path)
        return result
    with pytest.raises((EvidenceError, ValueError), match="identity|hash|changed|snapshot|Expecting|delimiter|unavailable"):
        returned.append(read_and_tamper())
    assert returned == [] and read_scope.CURRENT.get() is None


def test_no_cache_budget_means_nested_original_checks_are_not_deferred(graph_case, monkeypatch):
    c = graph_case
    monkeypatch.setattr(read_scope, "MAX_CACHE_BYTES", 0)
    calls = _hash_calls(monkeypatch)
    with verified_read() as scope:
        ws = load_workspace(c["workspace"])
        records, coverage, _ = evidence._collect(ws)
        assert not scope.sources
        before = len(calls)
        continuation._check_frozen_view(ws, records, coverage)
        assert len(calls) > before and scope.deferred_frozen_checks == 0


def test_partial_or_modified_view_cannot_borrow_registered_verification(graph_case):
    c = graph_case
    with verified_read() as scope:
        ws = load_workspace(c["workspace"])
        records, coverage, _ = evidence._collect(ws)
        partial = copy.deepcopy(coverage)
        partial[0]["status"] = "partial"
        assert not scope.defer_frozen_check(ws, records, partial)
        altered = copy.deepcopy(records)
        altered["science"].pop(c["claim"]["record_id"])
        assert not scope.defer_frozen_check(ws, altered, coverage)
        wrong_watermark = copy.deepcopy(coverage)
        wrong_watermark[0]["read_watermark"] += 1
        assert not scope.defer_frozen_check(ws, records, wrong_watermark)


def test_same_source_id_and_hash_in_other_workspace_does_not_defer_unregistered_path(graph_case, monkeypatch):
    c = graph_case
    second_source = c["entry"].parent / "other-source.sqlite"
    second_source.write_bytes(c["store"].path.read_bytes())
    config = json.loads(c["workspace"].read_text())
    config["workspace_id"] = "other-workspace"
    config["sources"][0]["location"] = str(second_source)
    other_workspace = c["entry"].parent / "other-workspace.json"
    other_workspace.write_text(json.dumps(config))
    calls = _hash_calls(monkeypatch)
    with verified_read() as scope:
        first = load_workspace(c["workspace"])
        records, coverage, _ = evidence._collect(first)
        assert scope.defer_frozen_check(first, records, coverage)
        other = load_workspace(other_workspace)
        rows, cov = evidence._source_uncached(other, other.source("science"))
        before = len(calls)
        continuation._check_frozen_view(other, {"science": rows}, [cov])
        assert str(second_source.absolute()) in calls[before:]
        assert scope.deferred_frozen_checks == 1


def test_first_cross_event_cache_admission_still_validates_before_reuse(graph_case, monkeypatch):
    c = graph_case
    with verified_read() as first:
        ws = load_workspace(c["workspace"])
        evidence._collect(ws)
    calls = _hash_calls(monkeypatch)
    with verified_read(first.snapshot()) as second:
        ws = load_workspace(c["workspace"])
        calls.clear()
        evidence._collect(ws)
        assert str(c["store"].path.absolute()) in calls
        assert second.reused_sources > 0 and second.deferred_frozen_checks == 0


def test_live_journal_view_is_not_registered_for_deferred_frozen_checks(tmp_path):
    path = tmp_path / "live.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE metadata(key TEXT PRIMARY KEY, body TEXT); CREATE TABLE events(sequence INTEGER PRIMARY KEY);")
        db.execute("INSERT INTO metadata VALUES (?,?)", ("resource:identity", json.dumps({"release": "fixture"})))
        db.execute("INSERT INTO metadata VALUES (?,?)", ("research:observation-1", json.dumps({"value": "fixture"})))
        db.execute("INSERT INTO events VALUES (1)")
    manifest = tmp_path / "workspace.json"
    manifest.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "live-fixture",
        "sources": [{"source_id": "science", "kind": "journal", "location": str(path),
            "identity": {"kind": "journal_identity", "key": "resource:identity", "value": {"release": "fixture"}}}]}))
    with verified_read() as scope:
        ws = load_workspace(manifest)
        records, coverage, warnings = evidence._collect(ws)
        assert not warnings and coverage[0]["status"] == "complete"
        assert not scope.sources
        assert not scope.defer_frozen_check(ws, records, coverage)
