"""The agent can prepare database candidates through one real stdio MCP server."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

from etalon.active.adapters import MOLECULAR_REPRESENTATION
from etalon.mcp._common import Cost, ok, threaded_tool, tool


def test_existing_artifact_is_not_a_transient_retry():
    @tool(Cost.CHEAP)
    def create() -> str:
        raise FileExistsError("already sealed")

    result = json.loads(create())
    assert result["error"]["code"] == "AlreadyExists"
    assert result["error"]["retryable"] is False


def test_threaded_data_work_does_not_block_the_mcp_event_loop():
    pytest.importorskip("anyio")
    started, release = threading.Event(), threading.Event()

    @threaded_tool(Cost.CHEAP)
    def work() -> str:
        started.set()
        if not release.wait(5):
            raise TimeoutError("event loop did not remain responsive")
        return ok(completed=True)

    async def exercise():
        task = asyncio.create_task(work())
        while not started.is_set():
            await asyncio.sleep(0.001)
        # The loop can still inspect state while the data call waits in another thread.
        release.set()
        return json.loads(await task)

    assert asyncio.run(exercise())["completed"] is True


def test_stdio_acquisition_to_campaign_uses_pinned_data_tools_and_skill_resources(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import McpError

    fixture = tmp_path / "catalog.csv"
    fixture.write_text("id,smiles\nfixture-ethanol,CCO\n")
    database = tmp_path / "campaign.sqlite"
    root = Path(__file__).resolve().parents[1]

    async def run():
        server = StdioServerParameters(command=sys.executable, args=["-m", "etalon.mcp"],
                                       env={"PYTHONPATH": str(root / "src")})
        async with (stdio_client(server) as (read, write),
                    ClientSession(read, write) as session):
            await session.initialize()
            names = {item.name for item in (await session.list_tools()).tools}
            assert {"etalon_data_run", "etalon_data_prepare", "etalon_data_import_candidates",
                    "etalon_screen_export", "etalon_active_create"} <= names
            resource = await session.read_resource("etalon://skills/molquarry/compound-sourcing")
            assert "MolQuarry" in resource.contents[0].text
            reference = await session.read_resource(
                "etalon://skills/molquarry/compound-sourcing/references/results.md")
            assert reference.contents[0].text
            index = await session.read_resource("etalon://skills/molquarry")
            workflows = json.loads(index.contents[0].text)["skills"]
            assert {row["name"] for row in workflows} == {
                "molquarry-target-modulators", "molquarry-compound-sourcing",
                "molquarry-analogue-search", "molquarry-selectivity-evidence",
                "molquarry-structure-templates", "molquarry-assay-literature",
            }
            for workflow in workflows:
                text = (await session.read_resource(workflow["uri"])).contents[0].text
                assert workflow["name"] in text
                for uri in workflow["references"]:
                    assert (await session.read_resource(uri)).contents[0].text
            # Only resources in the pinned skill index can be read.
            with pytest.raises(McpError):
                await session.read_resource("etalon://skills/molquarry/not-a-workflow")

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                assert not result.isError, result
                value = json.loads(result.content[0].text)
                assert value["ok"], value
                return value

            created = await call("etalon_active_create", {"database": str(database),
                "spec": {"objective": "kd", "budget": 0, "cost_unit": "fixture-quotes",
                         "representation": MOLECULAR_REPRESENTATION},
                "endpoints": [{"id": "kd", "target": "P00533", "quantity": "Kd", "units": "nM",
                    "protocol": "fixture-only", "cost": 0, "queryable": False, "requires_handoff": False}]})
            assert created["executed_actions"] == 0
            acquired = await call("etalon_data_run", {"request": {"kind": "import_catalog",
                "source": "chembl", "path": str(fixture),
                "options": {"source_version": "OFFLINE-FIXTURE-NOT-CHEMBL-DATA"}},
                "workspace": str(tmp_path), "run_id": "catalog", "max_requests": 0})
            assert acquired["usage"]["requests"] == 0
            assert acquired["file_count"] > 0 and "files" not in acquired
            prepared = await call("etalon_data_prepare", {"snapshot": acquired["snapshot"],
                "workspace": str(tmp_path), "run_id": "library", "id_field": "fields.id",
                "smiles_field": "fields.smiles"})
            imported = await call("etalon_data_import_candidates", {
                "database": str(database), "snapshot": prepared["snapshot"]})
            assert imported["added"] == 1
            inspected = await call("etalon_active_status", {"database": str(database)})
            assert inspected["state"]["candidates"] == 1
            assert inspected["state"]["observations"] == 0
            return len(names)

    assert asyncio.run(run()) >= 30
