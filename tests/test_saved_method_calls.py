"""Method discovery is actionable; saved execution still uses the original runtime."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from etalon.active import CampaignStore, CampaignSpec, Endpoint
from etalon.research import ResearchJournal
from etalon.research.notes import read_entry, save_progress, patch_progress, original
from etalon.research.resource_catalog import build_resource_catalog
from etalon.research.saved_methods import publish_method, resolve_saved_method
from etalon.runtime.api import submit_action, read_action_result


@pytest.fixture
def saved(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    entry = tmp_path / "ENTRY.json"
    contrary = tmp_path / "negative.json"
    contrary.write_text('{"scope":"software fixture; no scientific benefit measured"}')
    save_progress(ResearchJournal(store), entry, {"task_id": "methods", "branch_id": "main",
        "question": "Can a useful program be invoked without rewriting it?", "summary": "Fixture",
        "conditions": {"objective": "fixture"}, "evidence": [], "counterevidence": [original(contrary)],
        "resources": {"lesson": {"kind": "experience", "summary": "No scientific efficacy inferred"}}})
    script = tmp_path / "method.py"
    script.write_text('''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--result');a=p.parse_args()
r=json.loads(Path(a.request).read_text())
weight=json.loads(Path(r['inputs']['weights_path']).read_text())['weight']
Path(a.result).write_text(json.dumps({'schema':'etalon-method-result/1','summary':'Software fixture',
'outputs':{'value':sum(r['inputs']['values'])*r['inputs']['factor']*weight}}))
''')
    weights = tmp_path / 'weights.json'; weights.write_text('{"weight":1}')
    args = {"method_id": "fixture/v1", "command": [sys.executable, str(script)],
            "files": [str(script), str(weights)],
            "inputs": {"values": [1, 2, 3], "factor": 1, "weights_path":str(weights)}}
    result = publish_method(entry, "sum", args, summary="Sum with a tunable factor",
        conditions={"objective": "fixture"}, publication_id="publish-sum-v1")
    return entry, script, args, result


def test_published_method_is_discoverable_callable_and_versioned(saved):
    entry, script, args, published = saved
    items = build_resource_catalog(entry=entry, kinds=["method"])["items"]
    assert len(items) == 1
    assert items[0]["resolve"]["tool"] == "etalon_method_resolve"
    selected = items[0]["resolve"]["arguments"]
    resolved = resolve_saved_method(**selected, invocation={"request_id": "compute", "limits": {"wall_second": 30},
                                                          "inputs": {"factor": 4}})
    assert resolved["request"]["arguments"]["inputs"] == {**args['inputs'], "factor": 4}
    assert resolved["plan_id"] and not resolved["execution_performed"]
    note = read_entry(entry)["note"]
    assert read_entry(entry)['execution_permission'] == 'not_evaluated_by_reader'
    assert set(note["resources"]) == {"lesson", "sum"} and len(note["counterevidence"]) == 1
    patch_progress(entry, {"summary": "Later research"}, publication_id="later")
    replay = publish_method(entry, "sum", args, summary="Sum with a tunable factor",
        conditions={"objective": "fixture"}, publication_id="publish-sum-v1")
    assert replay["record_id"] == published["record_id"]
    assert read_entry(entry)["note"]["summary"] == "Later research"
    script.write_text("print('changed')")
    with pytest.raises(ValueError):
        resolve_saved_method(**selected, invocation={"request_id": "new", "limits": {"wall_second": 30}})
    # A stale unselected method does not prevent recording independent progress.
    # Its source is still checked when selected; scientific contrary evidence stays.
    patch_progress(entry, {'summary':'Other research continues'})
    assert read_entry(entry)['note']['summary'] == 'Other research continues'


def test_mcp_cli_share_the_exact_callable_contract(saved, tmp_path):
    from mcp.server.fastmcp import FastMCP
    from etalon.mcp.methods import register
    from etalon.mcp.evidence import register as register_evidence
    entry, _, args, _ = saved
    server = FastMCP("saved-methods")
    register(server)
    register_evidence(server)
    invocation = {"request_id": "same-call", "limits": {"wall_second": 30}}
    raw = asyncio.run(server.call_tool("etalon_method_resolve", {
        "entry": str(entry), "pointer": "/resources/sum", "invocation": invocation}))
    # FastMCP versions may provide a structured envelope alongside text.
    blocks = raw[0] if isinstance(raw, tuple) else raw
    body = json.loads(blocks[0].text)
    assert body["ok"] and body["plan_id"]
    call = tmp_path / "call.json"; call.write_text(json.dumps(invocation))
    cli = subprocess.run([sys.executable, "-m", "etalon", "research", "method", "--entry", str(entry),
        "--pointer", "/resources/sum", "--invocation", str(call)], capture_output=True, text=True)
    assert cli.returncode == 0, cli.stderr + cli.stdout
    assert json.loads(cli.stdout)["request"] == body["request"]
    raw = asyncio.run(server.call_tool("etalon_method_publish", {
        "entry": str(entry), "resource_id": "second", "arguments": args,
        "summary": "Same executable, explicit second resource", "conditions": {"objective": "fixture"}}))
    assert "second" in read_entry(entry)["note"]["resources"], raw
    update = {"entry":str(entry), "patch":{"summary":"Method used; retain scope"},
              "publication_id":"native-review", "resources":{"decision":{"kind":"experience","summary":"No new science inferred"}}}
    asyncio.run(server.call_tool('etalon_research_progress', update))
    rid = read_entry(entry)['record_id']
    asyncio.run(server.call_tool('etalon_research_progress', update))
    note = read_entry(entry)
    assert note['record_id'] == rid and len(note['note']['counterevidence']) == 1
    assert {'sum','second','lesson','decision'} <= note['note']['resources'].keys()
    raw = asyncio.run(server.call_tool('etalon_research_progress', {
        'entry':str(entry), 'patch':{'knowledge':{'summary':'A domain finding'}}}))
    blocks = raw[0] if isinstance(raw, tuple) else raw
    effects = json.loads(blocks[0].text)
    assert effects['patched_fields'] == ['knowledge']
    assert effects['continuation']['summary'] == 'Method used; retain scope'


def test_method_sources_survive_entry_handoff(saved, tmp_path):
    from etalon.research.handoff import plan_research_handoff, export_research_handoff
    entry, script, args, _ = saved
    target = tmp_path / "handoff"
    receipt = export_research_handoff(plan_research_handoff(entry), target)
    assert receipt
    script.unlink()
    Path(args['inputs']['weights_path']).unlink()
    resolved = resolve_saved_method(target / 'entry.json', '/resources/sum', invocation={
        'request_id':'relocated', 'limits':{'wall_second':30}})
    assert resolved['source']['verification'] == 'verified'
    assert resolved['request']['arguments']['command'][1] != str(script)
    assert Path(resolved['request']['arguments']['command'][1]).is_file()
    assert Path(resolved['request']['arguments']['inputs']['weights_path']).is_file()
    assert resolved['request']['arguments']['inputs']['weights_path'] != args['inputs']['weights_path']


@pytest.mark.skipif(os.name != "posix", reason="durable native action uses Linux /proc")
def test_saved_method_executes_once_and_new_reader_recovers(saved, tmp_path):
    entry, _, _, _ = saved
    selected = build_resource_catalog(entry=entry, kinds=["method"])["items"][0]
    call = resolve_saved_method(selected["content_ref"], selected["pointer"], invocation={
        "request_id": "once", "limits": {"wall_second": 30}, "inputs": {"factor": 4}})
    handle = submit_action(call["request"], workspace=tmp_path / "runtime", plan_id=call["plan_id"])
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        result = read_action_result(handle)
        if result["verification"]["state"] == "passed":
            break
        time.sleep(.1)
    assert result["verification"]["state"] == "passed", result
    assert result["outputs"][0]["result"]["outputs"]["value"] == 24
    assert result['rsi_review']['trigger'] == 'action_result_ready'
    assert result['rsi_review']['decision_key'] == read_action_result(handle)['rsi_review']['decision_key']
    again = submit_action(call["request"], workspace=tmp_path / "runtime", plan_id=call["plan_id"])
    assert again["job_id"] == handle["job_id"]
    handle_path = tmp_path / "handle.json"; handle_path.write_text(json.dumps(handle))
    read = subprocess.run([sys.executable, "-c",
        "import json,sys;from etalon.runtime.api import read_action_result;print(json.dumps(read_action_result(json.load(open(sys.argv[1])))))",
        str(handle_path)], capture_output=True, text=True)
    assert read.returncode == 0, read.stderr
    assert json.loads(read.stdout)["outputs"] == result["outputs"]


def _queued_invocation(saved, tmp_path, monkeypatch):
    """Real action store; keep the controller in-process in this portable test."""
    from etalon.runtime import service
    from etalon.research.saved_methods import call_saved_method
    launches = []
    monkeypatch.setattr(service, "_launch", lambda runtime, job: launches.append(job))
    entry = saved[0]
    invocation = {"request_id": "auto-note", "limits": {"wall_second": 30},
                  "inputs": {"factor": 4}, "max_seconds": 30}
    call = call_saved_method(entry, "/resources/sum", invocation=invocation, workspace=tmp_path / "auto-runtime")
    return call, invocation, launches


def test_optional_call_collect_executes_real_nonempty_abi_and_preserves_host_progress(saved, tmp_path, monkeypatch):
    from etalon.runtime import service
    from etalon.runtime.store import RuntimeStore
    from etalon.research.saved_methods import call_saved_method, collect_saved_method
    call, invocation, launches = _queued_invocation(saved, tmp_path, monkeypatch)
    entry = saved[0]
    assert call["note_publication"]["state"] == "recorded"
    assert read_entry(entry)["note"]["pending_handles"] == [call["handle"]]
    other_handle = {"schema": "fixture-handle/1", "request_id": "unrelated"}
    patch_progress(entry, {"summary": "Host is exploring another hypothesis",
                           "pending_handles": [call["handle"], other_handle]})

    # Only the controller launch is a fixture. The original method.run executes
    # the nonempty --request/--result program in a real native subprocess and
    # verifies its real receipt, artifacts and measured wall time.
    runtime = RuntimeStore(call["handle"]["workspace"])
    runtime.set_state("auto-note", 0, "running")
    deadline = time.monotonic() + 35
    def checkpoint():
        assert time.monotonic() < deadline, "fixture method did not finish"
    service._control(runtime, "auto-note", 0, checkpoint)
    runtime.heartbeat("auto-note", 0)
    finished = collect_saved_method(call["invocation_path"])
    assert finished["verification"]["state"] == "passed", finished
    assert finished["outputs"][0]["result"]["outputs"]["value"] == 24
    assert finished["accounting_refs"][0]["costs"]["wall_second"]["spent"] > 0
    current = read_entry(entry)
    assert current["note"]["summary"] == "Host is exploring another hypothesis"
    assert current["note"]["pending_handles"] == [other_handle]
    assert len(current["note"]["counterevidence"]) == 1
    receipt = json.loads(Path(finished["note_publication"]["receipt"]["path"]).read_text())
    assert receipt["scientific_admission"] == "not_inferred"
    assert receipt["result"]["outputs"] == finished["outputs"]
    patch_progress(entry, {"summary": "Host has interpreted the result"})
    later = read_entry(entry)["record_id"]
    sequence = runtime.get("auto-note")["sequence"]
    again = call_saved_method(entry, "/resources/sum", invocation=invocation, workspace=runtime.workspace)
    assert again["submission"] == "existing" and launches == ["auto-note"]
    assert read_entry(entry)["record_id"] == later
    assert runtime.get("auto-note")["sequence"] == sequence
    assert len([e for e in runtime.get("auto-note")["events"] if e["kind"] == "dispatched"]) == 1


def test_collect_recovers_committed_submit_without_resubmission(saved, tmp_path, monkeypatch):
    from etalon.research import method_invocations
    from etalon.research.saved_methods import collect_saved_method
    original_save = method_invocations._save
    def crash_after_submit(path, value, **kwargs):
        if Path(path).name == "handle.json":
            raise OSError("synthetic submitter disconnect")
        return original_save(path, value, **kwargs)
    monkeypatch.setattr(method_invocations, "_save", crash_after_submit)
    with pytest.raises(OSError, match="submitter disconnect"):
        _queued_invocation(saved, tmp_path, monkeypatch)
    monkeypatch.setattr(method_invocations, "_save", original_save)
    path = tmp_path / "auto-runtime/method-invocations/auto-note/invocation.json"
    recovered = collect_saved_method(path)
    assert recovered["handle"]["job_id"] == "auto-note"
    assert recovered["note_publication"]["state"] == "recorded"
    assert recovered["execution_performed"] is False
    assert read_entry(saved[0])["note"]["pending_handles"] == [recovered["handle"]]


def test_publication_failure_does_not_hide_handle_or_overwrite_later_judgment(saved, tmp_path, monkeypatch):
    from etalon.research import method_invocations
    from etalon.research.saved_methods import collect_saved_method
    original_save = method_invocations._save
    def crash_after_publication(path, value, **kwargs):
        if Path(path).name.endswith(".published.json"):
            raise OSError("synthetic publication marker failure")
        return original_save(path, value, **kwargs)
    monkeypatch.setattr(method_invocations, "_save", crash_after_publication)
    call, _, launches = _queued_invocation(saved, tmp_path, monkeypatch)
    assert call["note_publication"]["state"] == "pending"
    assert call["handle"]["job_id"] == "auto-note"
    patch_progress(saved[0], {"summary": "Later host interpretation"})
    before = read_entry(saved[0])["record_id"]
    monkeypatch.setattr(method_invocations, "_save", original_save)
    recovered = collect_saved_method(call["invocation_path"])
    assert recovered["note_publication"]["state"] == "recorded"
    assert read_entry(saved[0])["record_id"] == before
    assert read_entry(saved[0])["note"]["summary"] == "Later host interpretation"
    assert launches == ["auto-note"]


def test_changed_invocation_is_not_a_retry_and_collection_does_not_write_another_task(saved, tmp_path, monkeypatch):
    from etalon.research.saved_methods import call_saved_method, collect_saved_method
    call, invocation, launches = _queued_invocation(saved, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="different method invocation"):
        call_saved_method(saved[0], "/resources/sum", invocation={**invocation, "inputs": {"factor": 9}},
                          workspace=tmp_path / "auto-runtime")
    new_store = CampaignStore(tmp_path / "another.sqlite")
    new_store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    other = tmp_path / "another-entry.json"
    save_progress(ResearchJournal(new_store), other, {"task_id": "another", "branch_id": "main",
        "question": "Different task", "summary": "Do not attach the old computation", "conditions": {}})
    saved[0].write_bytes(other.read_bytes())
    before = saved[0].read_bytes()
    recovered = collect_saved_method(call["invocation_path"])
    assert recovered["note_publication"]["state"] == "pending"
    assert "different task" in recovered["note_publication"]["reason"]
    assert saved[0].read_bytes() == before and launches == ["auto-note"]


def test_saved_invocation_explicit_data_dependencies_and_duration(saved, tmp_path):
    data = tmp_path / "training.csv"
    data.write_text("x\n1\n")
    invocation = {"request_id": "bound-data", "limits": {"wall_second": 30}, "max_seconds": 42,
                  "inputs": {"training_csv": str(data)}, "input_files": {"training": str(data)}}
    before = resolve_saved_method(saved[0], "/resources/sum", invocation=invocation)
    data.write_text("x\n2\n")
    after = resolve_saved_method(saved[0], "/resources/sum", invocation=invocation)
    assert before["plan_id"] != after["plan_id"]
    assert after["request"]["max_seconds"] == 42
    assert after["request"]["arguments"]["inputs"]["training_csv"] == str(data)


def test_mcp_optional_call_and_collect_use_the_same_saved_state(saved, tmp_path, monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from etalon.mcp.methods import register
    from etalon.runtime import service
    launches = []
    monkeypatch.setattr(service, "_launch", lambda runtime, job: launches.append(job))
    server = FastMCP("saved-method-convenience")
    register(server)
    def tool(name, arguments):
        raw = asyncio.run(server.call_tool(name, arguments))
        blocks = raw[0] if isinstance(raw, tuple) else raw
        return json.loads(blocks[0].text)
    call = tool("etalon_method_call", {"entry": str(saved[0]), "pointer": "/resources/sum",
        "invocation": {"request_id": "mcp-once", "limits": {"wall_second": 30}},
        "workspace": str(tmp_path / "mcp-runtime")})
    assert call["ok"] and call["note_publication"]["state"] == "recorded"
    collected = tool("etalon_method_collect", {"invocation_path": call["invocation_path"]})
    assert collected["ok"] and collected["handle"] == call["handle"]
    assert collected["execution_performed"] is False and launches == ["mcp-once"]


def test_failed_callable_retains_real_cost_and_failure_without_reexecution(saved, tmp_path, monkeypatch):
    from etalon.research.saved_methods import call_saved_method, collect_saved_method
    from etalon.runtime import service
    from etalon.runtime.store import RuntimeStore
    script = tmp_path / "failed-method.py"
    script.write_text("raise RuntimeError('synthetic method failure after launch')\n")
    args = {"method_id": "failure-fixture/v1", "command": [sys.executable, str(script)],
            "files": [str(script)], "inputs": {"nonempty": [1, 2, 3]}}
    publish_method(saved[0], "failure", args, summary="Failure fixture", conditions={})
    launches = []
    monkeypatch.setattr(service, "_launch", lambda runtime, job: launches.append(job))
    call = call_saved_method(saved[0], "/resources/failure", invocation={"request_id": "failed-once",
                            "limits": {"wall_second": 30}}, workspace=tmp_path / "failure-runtime")
    runtime = RuntimeStore(call["handle"]["workspace"])
    runtime.set_state("failed-once", 0, "running")
    deadline = time.monotonic() + 35
    def checkpoint():
        assert time.monotonic() < deadline
    service._control(runtime, "failed-once", 0, checkpoint)
    result = collect_saved_method(call["invocation_path"])
    assert result["verification"]["state"] == "failed"
    assert result["outputs"][0]["result"]["status"] == "failed"
    assert result["outputs"][0]["result"]["outputs"] == {}
    assert result["accounting_refs"][0]["costs"]["wall_second"]["spent"] > 0
    assert result["note_publication"]["completed"] is True
    assert read_entry(saved[0])["note"]["pending_handles"] == []
    current = read_entry(saved[0])["record_id"]
    collect_saved_method(call["invocation_path"])
    assert read_entry(saved[0])["record_id"] == current and launches == ["failed-once"]


def test_unsubmitted_invocation_does_not_dispatch_after_note_changes_task(saved, tmp_path, monkeypatch):
    from etalon.research.saved_methods import call_saved_method, collect_saved_method
    from etalon.runtime import api
    submissions = []
    def interrupted(request, **kwargs):
        submissions.append(request["request_id"])
        raise OSError("fixture disconnected before dispatch")
    monkeypatch.setattr(api, "submit_action", interrupted)
    invocation = {"request_id": "never-started", "limits": {"wall_second": 30}}
    with pytest.raises(OSError, match="before dispatch"):
        call_saved_method(saved[0], "/resources/sum", invocation=invocation, workspace=tmp_path / "never-runtime")
    path = tmp_path / "never-runtime/method-invocations/never-started/invocation.json"
    assert collect_saved_method(path)["state"] == "not_submitted"
    value = json.loads(saved[0].read_text())
    saved[0].write_text(json.dumps({**value, "task_id": "another-task"}))
    with pytest.raises(ValueError, match="different task"):
        call_saved_method(saved[0], "/resources/sum", invocation=invocation, workspace=tmp_path / "never-runtime")
    assert submissions == ["never-started"]


@pytest.mark.parametrize("host_advanced", [False, True])
def test_journal_only_publication_is_resumed_or_merged_without_losing_handle(saved, tmp_path, monkeypatch, host_advanced):
    from etalon.research import notes
    from etalon.research.saved_methods import collect_saved_method
    original_publish = notes.publish_note
    stranded = []
    def fail_between_record_and_entry(entry, journal, record_id, **kwargs):
        if not stranded:
            stranded.append(record_id)
            raise OSError("synthetic disconnect after record_note before publish_note")
        return original_publish(entry, journal, record_id, **kwargs)
    monkeypatch.setattr(notes, "publish_note", fail_between_record_and_entry)
    before = read_entry(saved[0])["record_id"]
    call, _, launches = _queued_invocation(saved, tmp_path, monkeypatch)
    assert call["note_publication"]["state"] == "pending"
    assert read_entry(saved[0])["record_id"] == before
    assert read_entry(saved[0])["note"].get("pending_handles", []) == []
    if host_advanced:
        patch_progress(saved[0], {"summary": "Host made progress after the journal-only record"})
    recovered = collect_saved_method(call["invocation_path"])
    assert recovered["note_publication"]["state"] == "recorded"
    current = read_entry(saved[0])
    assert current["note"]["pending_handles"] == [call["handle"]]
    if host_advanced:
        assert current["note"]["summary"] == "Host made progress after the journal-only record"
        assert current["record_id"] != stranded[0]
    else:
        assert current["record_id"] == stranded[0]
    again = collect_saved_method(call["invocation_path"])
    assert again["note_publication"]["replayed"]
    assert read_entry(saved[0])["record_id"] == current["record_id"]
    assert launches == ["auto-note"]
