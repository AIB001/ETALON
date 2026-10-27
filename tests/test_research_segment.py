"""Producer/publication integration fixtures; not scientific performance evidence."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research import research_segment as segment
from test_research_publication import case, assert_old_unchanged


def response(case, *, next_segment=None):
    return {"summary": "Fixture measurement reused; remaining question is explicit",
        "measurements": {"saved_value": 1}, "failures": {"failed": 0, "attempted": 1},
        "decision": {"choice": "reconsider", "state": "active", "reason": "Fixture reanalysis opens a question",
                     "reconsider_when": "Fixture residual answered", "basis_refs": []},
        "residuals": ["Does the saved result answer the fixture residual?"], "next_segment": next_segment,
        "originals": [segment.result_file(case["result"], "original", source_kind="fixture")]}


def test_batch_auto_publication_new_process_read_and_same_operation_reuse(case, tmp_path):
    work = segment.work_from_entry("Answer the fixture residual", case["parent"])
    calls = []
    def producer(work, directory):
        # Represents a task-owned known batch, with no per-step host return.
        calls.extend(range(5))
        return response(case)
    kwargs = dict(producer=producer, method_id="fixture-batch/1", entry=case["entry"],
                  parent_entry=case["parent"], operation_id="one-batch")
    receipt = segment.run_segment(work, tmp_path / "segment", **kwargs)
    assert receipt["status"] == "published" and not receipt["producer_reused"]
    selected = segment.read_work(case["entry"])
    assert selected["work"]["goal"] == "Answer the fixture residual"
    assert selected["work"]["residuals"] == response(case)["residuals"]
    assert selected["work"]["status_guards"] == case["checkpoint"]["status_guards"]
    assert selected["execution_permission"] == "not_granted"
    code = "from etalon.research.research_segment import read_work; import json,sys; print(json.dumps(read_work(sys.argv[1])))"
    fresh = subprocess.run([sys.executable, "-c", code, str(case["entry"])], capture_output=True, text=True, check=True)
    assert json.loads(fresh.stdout)["work"]["residuals"] == selected["work"]["residuals"]
    before = file_identity(case["entry"])
    again = segment.run_segment(work, tmp_path / "segment", **kwargs)
    assert again["producer_reused"] and calls == list(range(5))
    assert file_identity(case["entry"]) == before
    assert_old_unchanged(case)


def test_publication_interruption_reuses_producer_response(case, tmp_path, monkeypatch):
    work = segment.work_from_entry("Fixture", case["parent"])
    calls = []
    def producer(*args):
        calls.append(True)
        return response(case)
    kwargs = dict(producer=producer, method_id="fixture/1", entry=case["entry"],
                  parent_entry=case["parent"], operation_id="interrupted")
    original = segment.resume_publication
    monkeypatch.setattr(segment, "resume_publication", lambda path: (_ for _ in ()).throw(RuntimeError("publication interrupted")))
    with pytest.raises(RuntimeError, match="interrupted"):
        segment.run_segment(work, tmp_path / "segment", **kwargs)
    monkeypatch.setattr(segment, "resume_publication", original)
    result = segment.run_segment(work, tmp_path / "segment", **kwargs)
    assert result["producer_reused"] and result["status"] == "published" and calls == [True]


def test_producer_unknown_outcome_and_changed_method_do_not_repeat(case, tmp_path):
    work = segment.work_from_entry("Fixture", case["parent"])
    calls = []
    def producer(*args):
        calls.append(True)
        raise RuntimeError("uncertain external outcome")
    kwargs = dict(producer=producer, method_id="fixture/1", entry=case["entry"],
                  parent_entry=case["parent"], operation_id="unknown")
    with pytest.raises(RuntimeError):
        segment.run_segment(work, tmp_path / "segment", **kwargs)
    with pytest.raises(EvidenceError, match="unknown producer outcome"):
        segment.run_segment(work, tmp_path / "segment", **kwargs)
    with pytest.raises(EvidenceError, match="content changed"):
        segment.run_segment(work, tmp_path / "segment", **{**kwargs, "method_id": "changed/1"})
    assert calls == [True]


def test_explicit_handle_recovery_does_not_rerun_producer(case, tmp_path):
    work = segment.work_from_entry('Fixture native recovery', case['parent'])
    calls = []
    def producer(work, directory):
        calls.append('launch')
        (directory/'native-handle.json').write_text('{"handle":"fixture-owned"}')
        raise SystemExit('Fixture host exits after publishing the native handle')
    def recover(work, directory):
        assert json.loads((directory/'native-handle.json').read_text())['handle']=='fixture-owned'
        calls.append('recover')
        return response(case)
    kwargs=dict(producer=producer,method_id='native-fixture/1',entry=case['entry'],
                parent_entry=case['parent'],operation_id='recover-native')
    with pytest.raises(SystemExit):
        segment.run_segment(work,tmp_path/'native',**kwargs)
    receipt=segment.run_segment(work,tmp_path/'native',recover=recover,**kwargs)
    assert receipt['producer_recovered'] and receipt['producer_wall_seconds'] is None
    assert receipt['recovery_wall_seconds']>=0 and calls==['launch','recover']
    again=segment.run_segment(work,tmp_path/'native',recover=recover,**kwargs)
    assert again['producer_reused'] and calls==['launch','recover']


def test_next_segment_is_executable_context_with_checked_dependencies(case, tmp_path):
    command = {"argv": [sys.executable, "-c", "print('fixture')"], "cwd": str(tmp_path),
        "reason": "Read saved fixture evidence", "ends_when": "One saved batch has been interpreted",
        "inputs": [{"path": str(case["result"]), "identity": file_identity(case["result"])}]}
    work = segment.work_from_entry("Fixture", case["parent"], next_segment=command)
    assert work["next_segment"] == command
    case["result"].write_text("changed", encoding="utf-8")
    with pytest.raises(EvidenceError, match="input changed"):
        segment.validate_work(work)


def test_no_silent_selection_of_unknown_branch_or_oversized_results(case, tmp_path):
    with pytest.raises(EvidenceError, match="existing branch"):
        segment.work_from_entry("Fixture", case["parent"], branch_id="missing")
    work = segment.work_from_entry("Fixture", case["parent"])
    result = response(case)
    result["measurements"] = {"accidental_raw_array": "x" * 70000}
    with pytest.raises(EvidenceError, match="64 KiB"):
        segment.publish_segment_result(result, work, tmp_path / "oversized", entry=case["entry"],
                                       parent_entry=case["parent"], operation_id="bad")
    assert not case["entry"].exists()


def test_entry_advance_during_producer_preserves_result_but_rejects_publication(case, tmp_path):
    work = segment.work_from_entry("Fixture", case["parent"])
    def producer(work, directory):
        case["entry"].parent.mkdir(parents=True)
        case["entry"].write_bytes(case["parent"].read_bytes())
        return response(case)
    with pytest.raises(EvidenceError, match="compare-and-swap"):
        segment.run_segment(work, tmp_path / "segment", producer=producer, method_id="fixture/1",
            entry=case["entry"], parent_entry=case["parent"], operation_id="concurrent")
    assert (tmp_path / "segment" / "producer-response.json").exists()
    assert case["entry"].read_bytes() == case["parent"].read_bytes()


def test_successor_can_cite_predecessor_work_without_ambiguous_current_work(case, tmp_path):
    work = segment.work_from_entry("Fixture", case["parent"])
    segment.publish_segment_result(response(case), work, tmp_path / "first", entry=case["entry"],
        parent_entry=case["parent"], operation_id="first")
    prior = segment.read_work(case["entry"])
    result = response(case)
    result["decision"]["basis_refs"] = [prior["work_ref"]]
    result["residuals"] = ["A new scientific residual"]
    segment.publish_segment_result(result, prior["work"], tmp_path / "second", entry=case["entry"], operation_id="second")
    current = segment.read_work(case["entry"])
    assert current["work"]["residuals"] == ["A new scientific residual"]
    assert current["work_ref"] != prior["work_ref"]
    current_identity = file_identity(case["entry"])
    historical_receipt = file_identity(tmp_path / "first" / "publication-receipt.json")
    replay = segment.publish_segment_result(response(case), work, tmp_path / "first", entry=case["entry"],
        parent_entry=case["parent"], operation_id="first")
    assert replay["status"] == "superseded"
    assert file_identity(case["entry"]) == current_identity
    assert file_identity(tmp_path / "first" / "publication-receipt.json") == historical_receipt


def test_new_counterevidence_survives_publication_without_bulk_read(case, tmp_path, monkeypatch):
    work = segment.work_from_entry("Fixture", case["parent"])
    result = response(case)
    result["counter_refs"] = [{"publication_ref": "original"}]
    segment.publish_segment_result(result, work, tmp_path / "negative", entry=case["entry"],
        parent_entry=case["parent"], operation_id="negative")
    workspace, checkpoint_ref = segment.load_entry(case["entry"])
    checkpoint = segment.read_content(workspace, checkpoint_ref)
    assert len(checkpoint["counter_refs"]) == len(case["checkpoint"]["counter_refs"]) + 1
    assert all(r in checkpoint["counter_refs"] for r in case["checkpoint"]["counter_refs"])
    reads = []
    actual = segment.read_evidence
    def observed(*args, **kwargs):
        reads.append((args[1], kwargs.get("view")))
        return actual(*args, **kwargs)
    monkeypatch.setattr(segment, "read_evidence", observed)
    selected = segment.read_work(case["entry"])
    bodies = [ref for ref, view in reads if view == "artifact"]
    assert bodies == [selected["work_ref"]]
    # Selection skips other bodies, not their storage or counterevidence links.
    item = actual(workspace, checkpoint["counter_refs"][-1], view="artifact")
    assert item["verification"]["artifact"] == "verified"
