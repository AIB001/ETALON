"""Selective saved calculations work for numeric and non-numeric research alike."""
import base64
import json

import numpy as np
import pytest

from etalon.__main__ import main
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research.evidence import EvidenceError, OriginalReader
from etalon.research.journal import ResearchJournal
from etalon.research.notes import (open_original, original, patch_progress, read_current,
                                   read_entry, read_original, resolve_method, save_progress)
from etalon.runtime.files import file_identity


@pytest.fixture
def project(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
                    [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    arrays = tmp_path / "calculations.npz"
    np.savez_compressed(arrays, decisions=np.arange(60000).reshape(20000, 3),
                        counter=np.array([False, True]), unselected=np.ones((400, 300)),
                        labels=np.array(["unknown", "excluded", "witness"]))
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"candidates": [{"id": i, "score": i / 10} for i in range(30)],
        "unmeasured_cost": None, "counterevidence": ["ordinary method was cheaper"],
        "huge": "not printed " * 5000}), encoding="utf-8")
    code = tmp_path / "solver.py"
    code.write_text("raise RuntimeError('description must never import the source')\n", encoding="utf-8")
    method = {"call": "solve(matrix, *, tolerance=1e-8)", "source": original(code),
              "limits": "fixture scope only", "qualification": "unvalidated", "fallback": "unknown"}
    note = {"task_id": "fixture", "branch_id": "main", "question": "Which saved results remain unknown?",
            "summary": "Keep both methods and their counterexamples", "conditions": {"fixture": True},
            "feedback": {"reusable_methods": [method]}, "evidence": [original(arrays), original(result), original(code)],
            "counterevidence": [original(result)]}
    entry = tmp_path / "entry.json"
    save_progress(journal, entry, note)
    return entry, note, arrays, result, code


def test_index_reads_headers_without_loading_arrays_and_selected_member_loads_once(project, monkeypatch):
    from etalon.research import evidence_refs
    entry, _, arrays, _, _ = project
    identities, loads = [], []
    identify, read_array = evidence_refs.file_identity, np.lib.format.read_array
    def counted_identity(path):
        identities.append(path)
        return identify(path)
    def counted_array(handle, *args, **kwargs):
        loads.append(handle.name)
        return read_array(handle, *args, **kwargs)
    monkeypatch.setattr(evidence_refs, "file_identity", counted_identity)
    monkeypatch.setattr(np.lib.format, "read_array", counted_array)
    with open_original(entry, "/evidence/0") as saved:
        index = saved.index(limit=2)
        assert index["next_offset"] == 2 and not loads
        assert index["items"][0]["shape"] == [20000, 3]
        assert "value" not in json.dumps(index)
        decisions = saved.array("decisions")
        assert not decisions.flags.writeable
        for i in range(100):
            assert saved.array("decisions")[i, 2] == i * 3 + 2
        assert saved.array("decisions") is decisions
        rows = saved.read(selector={"npz": [
            {"key": "decisions", "slice": [[1, 4], 2]},
            {"key": "decisions", "slice": [9, None]}]})
        assert rows["selection"]["items"][0]["value"] == [5, 8, 11]
        assert rows["selection"]["items"][1]["value"] == [27, 28, 29]
        assert loads == ["decisions.npy"]
    assert sum(path == arrays for path in identities) == 1
    with pytest.raises(EvidenceError, match="with scope"):
        saved.array("decisions")


def test_json_reuses_existing_pointer_paging_and_keeps_unknown_counterevidence(project):
    entry, note, _, result, _ = project
    before = file_identity(result)
    index = read_original(entry, "/evidence/1")
    assert {item["pointer"] for item in index["items"]} == {
        "/candidates", "/counterevidence", "/huge", "/unmeasured_cost"}
    page = read_original(entry, "/evidence/1", selector={"json": [
        "/candidates", "/unmeasured_cost", "/counterevidence", "/missing"]}, limit=2)
    rows, cost, counter, missing = page["selection"]["items"]
    assert rows["next_offset"] == 2 and len(rows["value"]) == 2
    assert cost["value"] is None and missing["status"] == "missing"
    assert counter["value"] == ["ordinary method was cheaper"]
    assert "not printed" not in json.dumps(page)
    assert file_identity(result) == before
    assert read_entry(entry)["note"]["counterevidence"] == note["counterevidence"]


def test_method_resolution_preserves_old_descriptor_without_import_or_unrelated_reads(project):
    entry, note, arrays, result, code = project
    old_pin = read_current(entry)["details"]
    patch_progress(entry, {"summary": "Later host judgment", "feedback": {"reusable_methods": [
        {"foreign_backend": "external solver", "source_ref": "remote:method-7", "scope": "declared"}]}})
    # Selected source reading does not open unrelated saved results.
    arrays.write_bytes(b"changed unrelated array")
    result.write_text("changed unrelated result", encoding="utf-8")
    selected = resolve_method(old_pin, "/feedback/reusable_methods/0")
    assert selected["description"] == note["feedback"]["reusable_methods"][0]
    assert selected["source"]["identity"] == file_identity(code)
    assert selected["details"]["record_id"] == old_pin["record_id"]
    assert selected["execution_performed"] is False
    external = resolve_method(entry, "/feedback/reusable_methods/0")
    assert external["description"]["source_ref"] == "remote:method-7"
    assert external["source_verification"].startswith("not_resolved")
    code.write_text("changed selected source", encoding="utf-8")
    with pytest.raises(EvidenceError, match="hash/size mismatch"):
        resolve_method(old_pin, "/feedback/reusable_methods/0")


def test_relocated_original_and_method_use_existing_handoff_mapping(project, tmp_path):
    from etalon.research.handoff import export_research_handoff, plan_research_handoff
    entry, _, arrays, result, code = project
    bundle = tmp_path / "portable"
    export_research_handoff(plan_research_handoff(entry), bundle)
    for path in (arrays, result, code):
        path.unlink()
    portable = bundle / "entry.json"
    method = resolve_method(portable, "/feedback/reusable_methods/0")
    assert str(bundle) in method["source"]["path"]
    with open_original(portable, "/evidence/0") as saved:
        assert saved.array("decisions")[6, 1] == 19


def test_changed_original_and_scope_mutation_are_rejected(project):
    entry, _, arrays, result, _ = project
    with pytest.raises(EvidenceError, match="changed during"):
        with open_original(entry, "/evidence/1") as saved:
            assert saved.json()["unmeasured_cost"] is None
            result.write_text("changed", encoding="utf-8")
    arrays.write_bytes(b"changed")
    with pytest.raises(EvidenceError, match="hash/size mismatch"):
        read_original(entry, "/evidence/0")


def test_array_limits_types_and_output_bounds_are_explicit(project, tmp_path):
    entry, _, _, _, _ = project
    with open_original(entry, "/evidence/0", max_loaded_bytes=32) as saved:
        assert saved.index()["items"][0]["array_bytes"] > 32
        with pytest.raises(EvidenceError, match="max_loaded_bytes"):
            saved.array("decisions")
    with open_original(entry, "/evidence/0") as saved:
        with pytest.raises(EvidenceError, match="smaller slice"):
            saved.read(selector={"npz": [{"key": "decisions"}]}, max_bytes=4096)
        result = saved.read(selector={"npz": [{"key": "labels", "slice": [[0, 2]]}]}, max_bytes=4096)
        assert result["selection"]["items"][0]["value"] == ["unknown", "excluded"]
    objects = tmp_path / "opaque.npz"
    np.savez(objects, opaque=np.array([{"not": "automatically loaded"}], dtype=object),
             nonfinite=np.array([np.nan, np.inf]))
    with OriginalReader(original(objects)) as saved:
        assert saved.index()["items"][0]["object_dtype"]
        with pytest.raises(EvidenceError, match="pickle is disabled"):
            saved.array("opaque")
        with pytest.raises(EvidenceError, match="not JSON compatible"):
            saved.read(selector={"npz": [{"key": "nonfinite"}]})
        assert np.isnan(saved.array("nonfinite")[0])


def test_cli_original_metadata_slices_and_method_do_not_execute(project, capsys, tmp_path):
    entry, note, arrays, _, _ = project
    def call(*args):
        status = main(["research", *map(str, args)])
        return status, json.loads(capsys.readouterr().out)
    status, index = call("original", "--entry", entry, "--pointer", "/evidence/0", "--limit", 2)
    assert status == 0 and index["items"][0]["key"] == "decisions"
    selector = tmp_path / "slice.json"
    selector.write_text('{"npz":[{"key":"decisions","slice":[[0,2],1]}]}', encoding="utf-8")
    status, page = call("original", "--entry", entry, "--pointer", "/evidence/0", "--selector", selector)
    assert status == 0 and page["selection"]["items"][0]["value"] == [1, 4]
    status, method = call("method", "--entry", entry, "--pointer", "/feedback/reusable_methods/0")
    assert status == 0 and method["description"] == note["feedback"]["reusable_methods"][0]
    status, raw = call("original", "--entry", entry, "--pointer", "/evidence/0", "--bytes", "--limit", 50)
    assert status == 0 and base64.b64decode(raw["fragment"]["content"]) == arrays.read_bytes()[:50]
    status, failed = call("original", "--entry", entry, "--pointer", "/summary")
    assert status == 2 and "original reference" in failed["error"]["message"]
