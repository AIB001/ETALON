"""Integration seams only: synthetic records and protocol calls are not science."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from etalon.mcp.server import costs, register_tools


class Collector:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def collect(function):
            assert function.__name__ not in self.tools
            self.tools[function.__name__] = function
            return function
        return collect


def test_action_and_event_mcp_delegate_without_a_second_implementation(monkeypatch):
    from etalon.runtime import api

    collected = Collector()
    register_tools(collected)
    calls = []
    request = {"schema": "etalon-action/1", "request_id": "fixture"}
    handle = {"workspace": "fixture", "request_id": "fixture"}
    log = {"kind": "campaign", "database": "fixture"}

    def prepare(value):
        calls.append(("prepare", value))
        return {"plan_id": "frozen-fixture"}

    def submit(value, *, workspace, plan_id):
        calls.append(("submit", value, workspace, plan_id))
        return handle

    def result(value):
        calls.append(("result", value))
        return {"execution_state": "unresolved", "observations": []}

    def events(value, *, after_sequence, limit):
        calls.append(("events", value, after_sequence, limit))
        return {"events": [], "next_cursor": {"after_sequence": after_sequence},
                "has_more": False}

    monkeypatch.setattr(api, "prepare_action", prepare)
    monkeypatch.setattr(api, "submit_action", submit)
    monkeypatch.setattr(api, "read_action_result", result)
    monkeypatch.setattr(api, "read_events", events)

    async def run():
        async def call(name, **args):
            return json.loads(await collected.tools[name](**args))
        assert (await call("etalon_action_prepare", request=request))["prepared"]["plan_id"]
        assert (await call("etalon_action_submit", request=request, workspace="fixture",
                           plan_id="frozen-fixture"))["handle"] == handle
        assert (await call("etalon_action_result", handle=handle))["result"] == {
            "execution_state": "unresolved", "observations": []}
        assert (await call("etalon_events_read", log_ref=log,
                           after_sequence=137, limit=17))["events"] == []

    asyncio.run(run())
    assert calls == [("prepare", request), ("submit", request, "fixture", "frozen-fixture"),
                     ("result", handle), ("events", log, 137, 17)]


def test_method_mcp_uses_existing_journal_and_does_not_create_observations(tmp_path):
    from etalon.active import CampaignSpec, CampaignStore, Endpoint
    from etalon.research.journal import ResearchJournal

    database = tmp_path / "fixture.sqlite"
    store = CampaignStore(database)
    store.configure(CampaignSpec("unmeasured", 10, "credit", "integration-fixture"),
                    [Endpoint("unmeasured", "fixture", "not_measured", "count", "fixture/1",
                              1, queryable=False)])
    collector = Collector()
    register_tools(collector)
    record = {"schema": "etalon-method-version/1", "name": "fixture-method",
              "roles": ["domain_skill"], "content_ref": {"manifest_sha256": "a" * 64},
              "parents": [], "dependencies": {}, "entrypoint": None,
              "change_summary": "Synthetic provenance only", "evidence_refs": [],
              "verification_basis": "host_reported"}
    response = json.loads(collector.tools["etalon_method_record"](
        database=str(database), record=record))
    assert response["ok"], response
    identifier = response["record_id"]
    assert response["record"] == ResearchJournal(store).get(identifier)
    before = store.events()
    repeated = json.loads(collector.tools["etalon_method_record"](
        database=str(database), record=record))
    assert repeated["record_id"] == identifier
    assert store.events() == before
    read = json.loads(collector.tools["etalon_method_read"](
        database=str(database), record_id=identifier))
    assert read["record"] == response["record"]
    bad = json.loads(collector.tools["etalon_method_record"](
        database=str(database), record={"schema": "arbitrary-metadata/1"}))
    assert not bad["ok"] and not bad["error"]["retryable"]
    assert not store.observations()


def test_real_stdio_discovery_and_invalid_action_are_consistent():
    """A real MCP protocol client, explicitly not a Codex/Claude decision trace."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "etalon.mcp"],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                 "PYTHONUTF8": "1"},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                names = [item.name for item in (await session.list_tools()).tools]
                assert len(names) == len(set(names))
                assert set(names) == set(costs())
                result = await session.call_tool("etalon_action_prepare", {
                    "request": {"schema": "etalon-action/1", "request_id": "unsupported",
                                "operation": "not.installed", "arguments": {}, "limits": {}}})
                value = json.loads(result.content[0].text)
                assert not value["ok"] and not value["error"]["retryable"]
                capabilities = await session.call_tool("etalon_capabilities", {})
                assert json.loads(capabilities.content[0].text)["ok"]

    asyncio.run(run())


def test_cli_sdk_and_mcp_event_cursor_agree(tmp_path):
    """Each public entry consumes the same committed events and rejects a foreign cursor."""
    from etalon.active import CampaignSpec, CampaignStore, Endpoint
    from etalon.runtime.api import read_events

    database = tmp_path / "events.sqlite"
    store = CampaignStore(database)
    store.configure(CampaignSpec("fixture", 3, "credit", "fixture"),
                    [Endpoint("fixture", "fixture", "unmeasured", "count", "fixture/1",
                              1, queryable=False)])
    log = {"kind": "campaign", "database": str(database)}
    config = tmp_path / "log.json"
    config.write_text(json.dumps(log), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
           "PYTHONUTF8": "1"}
    command = [sys.executable, "-m", "etalon", "action", "events", "--config", str(config)]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    cli = json.loads(result.stdout)
    sdk = read_events(log)
    assert cli == {"ok": True, **sdk}
    collector = Collector()
    register_tools(collector)
    mcp = json.loads(asyncio.run(collector.tools["etalon_events_read"](log_ref=log)))
    assert mcp == cli
    cursor = tmp_path / "cursor.json"
    cursor.write_text(json.dumps({**sdk["next_cursor"], "log_id": "foreign"}),
                      encoding="utf-8")
    refused = subprocess.run(command + ["--cursor", str(cursor)], cwd=tmp_path, env=env,
                             capture_output=True, text=True)
    assert refused.returncode == 1
    assert json.loads(refused.stdout)["error"]["code"] == "StateError"
    refused_mcp = json.loads(asyncio.run(collector.tools["etalon_events_read"](
        log_ref=log, after_sequence=json.loads(cursor.read_text()))))
    assert refused_mcp["error"]["code"] == "StateError"
    assert not refused_mcp["error"]["retryable"]
