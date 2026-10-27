"""Actionable feedback closes actual producer/note publication; fixture science only."""
import copy
import json
import sys

import pytest

from etalon.research import research_segment as segment
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.notes import patch_progress, publish_note, read_entry, record_note
from test_research_notes import case as note_case
from test_research_publication import case
from test_research_segment import response


def feedback(result_path, tmp_path):
    command = {"argv": [sys.executable, "-c", "print('fixture batch')"], "cwd": str(tmp_path),
        "reason": "Test the remaining fixture contrast", "ends_when": "A counter case changes the choice",
        "inputs": [{"path": str(result_path), "identity": file_identity(result_path)}]}
    return {"contrasts": [{"metric": "fixture loss", "baseline": 2, "candidate": 1,
                          "status": "measured", "scope": "fixture cases only"}],
        "conditions": {"fixture": "fixed input"},
        "counter_cases": [{"case": "changed input", "status": "failed", "difference": 3}],
        "untested": [{"case": "new input", "value": None, "cost": None}],
        "actions": [{"id": "scoped", "command": command,
            "replacement": {"fidelity": "decision_equivalent", "scope": "fixture ordering only",
                            "fallback_action": "retain", "generalization": "untested"}},
            {"id": "retain", "command": None}], "selected_action": "scoped"}


def test_graph_feedback_and_bounded_history_survive_publication(case, tmp_path):
    work = segment.work_from_entry("Fixture contrast", case["parent"])
    work["completed_refs"] = [f"historical-reference-{i}" for i in range(1000)]
    result = response(case)
    result["feedback"] = feedback(case["result"], tmp_path)
    result["next_segment"] = result["feedback"]["actions"][0]["command"]
    segment.publish_segment_result(result, work, tmp_path / "first", entry=case["entry"],
        parent_entry=case["parent"], operation_id="first")
    current = segment.read_work(case["entry"])["work"]
    assert current["feedback"] == result["feedback"]
    assert current["next_segment"] == result["next_segment"]
    assert len(current["completed_refs"]) == 1
    prior = current["previous_work"]
    assert file_identity(prior["path"]) == prior["identity"]
    assert json.loads(open(prior["path"], encoding="utf-8").read()) == work
    assert len(json.dumps(current)) < 5000
    next_result = response(case)
    segment.publish_segment_result(next_result, current, tmp_path / "second", entry=case["entry"], operation_id="second")
    successor = segment.read_work(case["entry"])["work"]
    assert "feedback" not in successor
    assert len(successor["completed_refs"]) == 1
    assert json.loads(open(successor["previous_work"]["path"], encoding="utf-8").read())["feedback"] == result["feedback"]


def note_entry(note_case, tmp_path):
    journal, note, original = note_case
    note.update(state="paused", authorization="fixture execution only", residuals=["Initial contrast"])
    entry = tmp_path / "ENTRY.json"
    rid = record_note(journal, note, tmp_path / "memory")
    publish_note(entry, journal, rid)
    return entry, original


def test_note_producer_feedback_next_producer_and_replay_without_graph(note_case, tmp_path, monkeypatch):
    entry, original = note_entry(note_case, tmp_path)
    monkeypatch.setattr(segment, "load_entry", lambda *a: pytest.fail("note route must not load a graph"))
    monkeypatch.setattr(segment, "prepare_result_publication", lambda **kw: pytest.fail("note route must not publish a graph"))
    work = segment.work_from_entry("Answer fixture contrast", entry)
    result = response({"result": original})
    result["feedback"] = feedback(original, tmp_path)
    result["next_segment"] = result["feedback"]["actions"][0]["command"]
    result["counter_refs"] = [{"publication_ref": "original"}]
    calls = []
    def producer(current, directory):
        calls.append(current["residuals"])
        return result
    kwargs = dict(producer=producer, method_id="fixture-batch/1", entry=entry, operation_id="first")
    receipt = segment.run_segment(work, tmp_path / "first", **kwargs)
    assert receipt["status"] == "published"
    first = read_entry(entry)
    assert first["note"]["feedback"] == result["feedback"]
    assert first["note"]["state"] == "paused"
    assert len(first["note"]["counterevidence"]) == 1
    current = segment.read_work(entry)["work"]
    assert current["feedback"]["untested"][0]["value"] is None
    assert current["source_record_id"] == first["record_id"]
    assert current["next_segment"] == result["next_segment"]
    assert current["state"] == "paused" and current["execution_permission"] == "not_granted"
    def next_producer(current, directory):
        assert current["feedback"] == result["feedback"]
        calls.append(current["feedback"]["selected_action"])
        return response({"result": original})
    segment.run_segment(current, tmp_path / "second", producer=next_producer, method_id="fixture-batch/2",
        entry=entry, operation_id="second")
    second = read_entry(entry)
    assert second["note"]["supersedes"] == [first["record_id"]]
    assert len(second["note"]["evidence"]) == len(first["note"]["evidence"])
    assert second["note"]["feedback"] is None
    replay = segment.run_segment(work, tmp_path / "first", **kwargs)
    assert replay["producer_reused"] and replay["status"] == "superseded"
    assert len(calls) == 2 and read_entry(entry)["record_id"] == second["record_id"]


def test_current_host_feedback_overrides_stored_work_and_old_replay(note_case, tmp_path):
    entry, original = note_entry(note_case, tmp_path)
    work = segment.work_from_entry("Fixture", entry)
    result = response({"result": original})
    result["feedback"] = feedback(original, tmp_path)
    result["next_segment"] = result["feedback"]["actions"][0]["command"]
    segment.publish_segment_result(result, work, tmp_path / "first", entry=entry, operation_id="first")
    update = patch_progress(entry, {"summary": "Host found a counterexample", "residuals": ["New counterexample"],
        "next_action": None, "feedback": {"counter_cases": ["host counter"], "selected_action": None},
        "constraints": ["Keep stopped"]}, publication_id="host")
    replay = segment.publish_segment_result(result, work, tmp_path / "first", entry=entry, operation_id="first")
    current = segment.read_work(entry)["work"]
    assert replay["status"] == "superseded" and current["source_record_id"] == update["record_id"]
    assert current["residuals"] == ["New counterexample"] and current["next_segment"] is None
    assert current["feedback"]["counter_cases"] == ["host counter"]
    assert current["constraints"] == ["Keep stopped"]

    # A direct host override does not have to rewrite earlier method feedback.
    patch_progress(entry, {"feedback": result["feedback"], "next_action": "Inspect the counterexample first"})
    direct = segment.read_work(entry)["work"]
    assert direct["feedback"] == result["feedback"]
    assert direct["next_segment"] is None and direct["next_action"] == "Inspect the counterexample first"
    assert direct["feedback_selection_status"] == "superseded_by_host"
    assert read_entry(entry)["note"]["feedback"] == result["feedback"]


def test_note_state_tracks_resolved_decision_and_plain_next_action_stays_nonexecuting(note_case, tmp_path):
    entry, original = note_entry(note_case, tmp_path)
    patch_progress(entry, {"state": "active", "next_action": "Consider a different mechanism"})
    work = segment.read_work(entry)["work"]
    assert work["next_segment"] is None
    result = response({"result": original})
    result["decision"].update(state="resolved", choice="retain")
    segment.publish_segment_result(result, work, tmp_path / "resolved", entry=entry, operation_id="resolved")
    assert read_entry(entry)["note"]["state"] == "resolved"
    assert segment.read_work(entry)["work"]["branch_state"] == "resolved"


def test_note_concurrent_host_progress_preserves_producer_output_without_retry(note_case, tmp_path):
    entry, original = note_entry(note_case, tmp_path)
    work = segment.work_from_entry("Fixture", entry)
    calls = []
    def producer(current, directory):
        calls.append(True)
        patch_progress(entry, {"summary": "Host changed interpretation"}, publication_id="host")
        return response({"result": original})
    kwargs = dict(producer=producer, method_id="fixture/1", entry=entry, operation_id="racing")
    for _ in range(2):
        with pytest.raises(ValueError, match="expected record"):
            segment.run_segment(work, tmp_path / "racing", **kwargs)
    assert calls == [True]
    assert (tmp_path / "racing" / "producer-response.json").exists()
    assert read_entry(entry)["note"]["summary"] == "Host changed interpretation"


@pytest.mark.parametrize("problem", ["duplicate", "unknown", "different_command"])
def test_feedback_choice_must_be_the_published_successor(case, tmp_path, problem):
    result = response(case)
    result["feedback"] = feedback(case["result"], tmp_path)
    result["next_segment"] = copy.deepcopy(result["feedback"]["actions"][0]["command"])
    if problem == "duplicate":
        result["feedback"]["actions"].append(result["feedback"]["actions"][0])
    elif problem == "unknown":
        result["feedback"]["selected_action"] = "absent"
    else:
        result["next_segment"]["reason"] = "Different scientific question"
    with pytest.raises(EvidenceError, match="action"):
        segment._validate_result(result)


def test_unselected_unrun_action_does_not_block_current_retain(case, tmp_path):
    result = response(case)
    result["feedback"] = feedback(case["result"], tmp_path)
    result["feedback"]["actions"][0]["command"]["inputs"][0]["path"] = str(tmp_path / "not-yet-produced.json")
    result["feedback"]["selected_action"] = "retain"
    saved = segment._validate_result(result)
    assert saved["next_segment"] is None
    assert saved["feedback"]["untested"] == [{"case": "new input", "value": None, "cost": None}]
