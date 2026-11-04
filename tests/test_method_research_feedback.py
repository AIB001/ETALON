"""Small real method executions must return useful next-action evidence."""
import json
from pathlib import Path
import sys
import time

import pytest

from etalon.active import CampaignStore, CampaignSpec, Endpoint
from etalon.research import ResearchJournal
from etalon.research.notes import save_progress
from etalon.research.saved_methods import publish_method, call_saved_method, collect_saved_method
from etalon.research.method_tools import crossfit_predictions
from etalon.runtime import api, service
from etalon.runtime.store import RuntimeStore


def execute_method(tmp_path, monkeypatch, code):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    entry = tmp_path / "ENTRY.json"
    save_progress(ResearchJournal(store), entry, {"task_id": "feedback", "branch_id": "main",
        "question": "Can a program's next-action evidence survive ordinary collection?",
        "summary": "Software fixture only", "evidence": [], "counterevidence": [], "conditions": {}})
    script = tmp_path / "method.py"
    script.write_text(code, encoding="utf-8")
    publish_method(entry, "program", {"method_id": "feedback-fixture/v1",
        "command": [sys.executable, "-X", "utf8", str(script)], "files": [str(script)], "inputs": {}},
        summary="Feedback fixture", conditions={})
    launches = []
    monkeypatch.setattr(service, "_launch", lambda runtime, job: launches.append(job))
    call = call_saved_method(entry, "/resources/program", invocation={"request_id": "once",
        "limits": {"wall_second": 30}}, workspace=tmp_path / "runtime")
    runtime = RuntimeStore(call["handle"]["workspace"])
    runtime.set_state("once", 0, "running")
    deadline = time.monotonic() + 35
    def checkpoint():
        assert time.monotonic() < deadline, "method subprocess did not finish"
    service._control(runtime, "once", 0, checkpoint)
    return call, runtime, launches


def test_failed_method_returns_actual_bounded_exception_and_real_cost(tmp_path, monkeypatch):
    call, runtime, launches = execute_method(tmp_path, monkeypatch,
        "import sys\nsys.stderr.write('preceding diagnostic\\n' * 1000)\n"
        "raise ValueError('真实错误: incompatible dose units')\n")
    result = collect_saved_method(call["invocation_path"])
    body = api.read_action_result(call["handle"])["outputs"][0]["result"]
    assert body["status"] == "failed"
    assert body["failure"]["returncode"] != 0
    assert "ValueError: 真实错误: incompatible dose units" in body["failure"]["stderr_tail"]
    assert body["failure"]["stderr_truncated"] is True
    assert len(body["failure"]["stderr_tail"].encode("utf-8")) < 2200
    assert result["output_summaries"][0]["summary"]["failure"] == body["failure"]
    assert result["accounting_refs"][0]["costs"]["wall_second"]["spent"] > 0
    before = runtime.get("once")["sequence"]
    again = collect_saved_method(call["invocation_path"])
    assert again["output_summaries"] == result["output_summaries"]
    assert runtime.get("once")["sequence"] == before and launches == ["once"]


def test_bulk_result_keeps_summary_and_feedback_without_resending_arrays(tmp_path, monkeypatch):
    call, runtime, launches = execute_method(tmp_path, monkeypatch, '''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--result');a=p.parse_args()
Path(a.result).write_text(json.dumps({'schema':'etalon-method-result/1',
'summary':'Selection is unchanged; the remaining ambiguity concerns calibration.',
'outputs':{'scores':list(range(20000))},
'feedback':{'selected':['candidate-b'],'uncertainty':'not calibrated'}}),encoding='utf-8')
''')
    result = collect_saved_method(call["invocation_path"])
    assert isinstance(result["outputs"], dict)  # Legacy bulk locator stays compatible.
    preview = result["output_summaries"][0]
    assert preview["summary"]["summary"].startswith("Selection is unchanged")
    assert preview["summary"]["feedback"] == {"selected": ["candidate-b"], "uncertainty": "not calibrated"}
    assert "scores" not in json.dumps(preview)
    assert len(json.dumps(result).encode()) < 12000
    assert preview["read"]["tool"] == "etalon_workflow_artifact"
    full = api.read_action_result(call["handle"])
    assert len(full["outputs"][0]["result"]["outputs"]["scores"]) == 20000
    compact = api.read_action_result(call["handle"], view="summary")
    assert compact["outputs"] == result["output_summaries"]
    assert compact["accounting_refs"] == full["accounting_refs"]
    assert launches == ["once"]


def test_large_feedback_remains_explicitly_addressable_and_source_unchanged():
    output = {"kind": "runtime_result", "workspace": "/workspace", "job_id": "job", "node_id": "action",
        "sha256": "fixture", "result": {"status": "completed", "summary": "Short decision",
        "feedback": {"details": "x" * 10000}, "outputs": {"values": list(range(100))}}}
    before = json.dumps(output)
    preview = api._output_summary(output)
    assert "feedback" not in preview["summary"]
    assert preview["feedback_read"]["member"] == "/feedback"
    assert preview["read"]["job_id"] == "job"
    assert json.dumps(output) == before
    assert len(json.dumps(preview)) < 1500


@pytest.mark.parametrize("missing", [None, float("nan")])
def test_missing_fold_never_runs_an_incomplete_crossfit(missing):
    calls = []
    def predict(train, features):
        calls.append(train)
        return [0] * len(features)
    rows = [{"x": i, "target": i} for i in range(4)]
    with pytest.raises(ValueError, match="fold"):
        crossfit_predictions(rows, target="target", features=["x"],
                             folds=[0, missing, 1, 1], fit_predict=predict)
    assert calls == []  # Validate all assignments before spending on any fit.
