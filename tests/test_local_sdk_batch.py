"""Real durable SDK reads of explicitly synthetic receipts, without worker mocks."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from etalon.runtime import api
from etalon.runtime.store import RuntimeStore

EXAMPLE = Path(__file__).parents[1] / "examples/local_sdk_batch.py"
spec = importlib.util.spec_from_file_location("local_sdk_batch_example", EXAMPLE)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def build_batch_fixture(root: Path):
    """Persist synthetic completed/failed/unknown states, not actual dispatches."""
    runtime = RuntimeStore(root / "runtime", create=True)
    handles = {}
    for key, state in (("ok", "succeeded"), ("paid-failure", "failed"), ("unknown", "reconciliation_required")):
        request = {"schema": "etalon-action/1", "request_id": key, "operation": "screen.export",
                   "arguments": {"workspace": str(root), "run_id": "fixture-not-executed"},
                   "limits": {"fixture_credit": 2}, "max_seconds": 60,
                   "method_use_refs": [], "evidence_refs": []}
        prepared = api.prepare_action(request)
        runtime.create(key, prepared)
        node = runtime.get(key)["nodes"][0]["definition"]
        runtime.start_node(key, 0, node, request["arguments"], {"fixture": "state setup only; no tool executed"})
        if state == "reconciliation_required":
            runtime.unresolved(key, 0, "action", "fixture response lost; outcome and final cost unknown")
        else:
            result = {"status": "FAILED" if state == "failed" else "SUCCEEDED",
                      "fixture": True, "long_evidence": "synthetic original detail " * 1000}
            runtime.finish_node(key, 0, "action", result, {"fixture": True}, {"fixture_credit": 1.25},
                                basis={"fixture_credit": "synthetic declared charge; not measured compute"},
                                state="failed" if state == "failed" else "verified",
                                error="fixture validation failed after charge" if state == "failed" else None)
        runtime.set_state(key, 0, state)
        handles[key] = api._action_handle(runtime.get(key))
    return runtime, handles


def test_local_batch_preserves_failure_unknown_full_evidence_and_continues(tmp_path):
    runtime, handles = build_batch_fixture(tmp_path)
    before = {key: runtime.get(key) for key in handles}
    invalid = {**handles["ok"], "plan_id": "wrong-identity"}
    batch = [handles["paid-failure"], invalid, handles["unknown"], handles["ok"], handles["paid-failure"]]
    summaries = example.read_action_results(batch, tmp_path / "snapshots")
    assert [s["execution_state"] for s in summaries] == ["failed", "unknown", "unresolved", "completed", "failed"]
    assert summaries[1]["read_status"] == "failed" and summaries[1]["accounting_refs"] is None
    failed_cost = summaries[0]["accounting_refs"][0]["costs"]["fixture_credit"]
    assert failed_cost["spent"] == 1.25 and "synthetic" in failed_cost["basis"]
    unknown_cost = summaries[2]["accounting_refs"][0]["costs"]["fixture_credit"]
    assert unknown_cost["spent"] is None and unknown_cost["reserved"] == 2
    assert summaries[2]["recovery"]["required"] is True
    assert summaries[2]["recovery"]["automatic_retry"] is False
    assert summaries[0]["verification"]["state"] == "failed"
    for handle, summary in zip(batch, summaries, strict=True):
        ref = summary["full_result"]
        raw = Path(ref["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"] and len(raw) == ref["bytes"]
        payload = json.loads(raw)
        assert payload["handle"] == handle
        if summary["read_status"] == "ok":
            assert payload["result"] == api.read_action_result(handle)
    assert summaries[0]["full_result"]["sha256"] == summaries[4]["full_result"]["sha256"]
    assert summaries[4]["same_execution_as_item"] == 1
    assert {key: runtime.get(key) for key in handles} == before
    assert len(json.dumps(summaries)) < sum(s["full_result"]["bytes"] for s in summaries)
    with pytest.raises(FileExistsError):
        example.read_action_results(batch, tmp_path / "snapshots")
    assert {key: runtime.get(key) for key in handles} == before


def test_example_cli_reads_real_handles_and_only_prints_summary(tmp_path):
    runtime, handles = build_batch_fixture(tmp_path)
    path = tmp_path / "handles.json"
    path.write_text(json.dumps(list(handles.values())), encoding="utf-8")
    completed = subprocess.run([sys.executable, str(EXAMPLE), "--handles", str(path),
                                "--output", str(tmp_path / "cli-results")],
                               capture_output=True, text=True, timeout=30, check=True)
    summary = json.loads(completed.stdout)
    assert len(summary) == 3 and "long_evidence" not in completed.stdout
    assert runtime.get("unknown")["resources"]["fixture_credit"]["reserved"] == 2
    assert runtime.get("paid-failure")["resources"]["fixture_credit"]["spent"] == 1.25
