"""A real MCP client authors, executes and inspects bounded CPU workflows.

Molecular weight is a deterministic execution fixture, not biological activity.
All catalog input is local and network request budgets are explicitly zero.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from etalon.active.adapters import MOLECULAR_REPRESENTATION
from etalon.campaign.design import component
from etalon.mcp.server import RUNTIME_RESOURCE, costs
from etalon.runtime.cli import _read

ROOT = Path(__file__).resolve().parents[1]


def design():
    return {"schema": "etalon-cascade-design/1", "name": "mcp-cpu-properties", "tiers": [
        {"id": "measure", "title": "Explicit CPU molecular properties", "criteria": [
            component("properties", "features.rdkit_properties@0.1.0")]}],
        "finalize": {"steps": [{"id": "export", "backend": "export.rdkit_sdf_shortlist@0.1.0",
                                "settings": {"schema_version": 1}}]}}


def acquisition(fixture):
    return {"id": "acquire", "operation": "data.acquire", "arguments": {"request": {
        "kind": "import_catalog", "source": "chembl", "path": str(fixture),
        "options": {"source_version": "OFFLINE-FIXTURE-NOT-CHEMBL-DATA"}},
        "budget": {"max_requests": 0, "max_bytes": 1_000_000, "max_seconds": 30},
        "min_records": 3}, "resources": {"http_requests": 0, "http_bytes": 1_000_000}}


def test_stdio_external_workflow_and_registered_active_cpu_execution(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    fixture = tmp_path / "catalog.csv"
    fixture.write_text("id,smiles\nethanol,CCO\naspirin,CC(=O)Oc1ccccc1C(=O)O\n"
                       "acetate,CC(=O)[O-].[Na+]\n", encoding="utf-8")
    database, workspace = str(tmp_path / "campaign.sqlite"), str(tmp_path / "project")

    async def run():
        server = StdioServerParameters(command=sys.executable, args=["-m", "etalon.mcp"],
                                       env={"PYTHONPATH": str(ROOT / "src")})
        async with (stdio_client(server) as (read, write), ClientSession(read, write) as session):
            await session.initialize()
            names = {item.name for item in (await session.list_tools()).tools}
            assert RUNTIME_RESOURCE in {str(item.uri) for item in (await session.list_resources()).resources}
            assert {"etalon_executor_prepare", "etalon_executor_register", "etalon_executor_list",
                    "etalon_screen_configure", "etalon_workflow_capabilities", "etalon_workflow_plan",
                    "etalon_workflow_submit", "etalon_workflow_status", "etalon_workflow_observe",
                    "etalon_workflow_advance", "etalon_workflow_cancel", "etalon_workflow_reconcile",
                    "etalon_workflow_artifact", "etalon_active_execution_plan", "etalon_active_submit"} <= names

            async def call(name, arguments=None, *, accepted=True):
                result = await session.call_tool(name, arguments or {})
                assert not result.isError, result
                value = json.loads(result.content[0].text)
                assert value["ok"] is accepted, value
                return value

            async def settled(job_id):
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    job = (await call("etalon_workflow_status", {"workspace": workspace,
                                                               "job_id": job_id}))["job"]
                    if job["state"] not in {"queued", "starting", "running"}:
                        return job
                    await asyncio.sleep(0.15)
                pytest.fail(f"workflow {job_id} did not settle: {job}")

            capabilities = await call("etalon_workflow_capabilities")
            assert {"data.acquire", "data.prepare", "screen.run", "active.run"} <= capabilities["operations"].keys()
            configured = await call("etalon_screen_configure", {"workspace": workspace,
                                                                 "configuration": design()})
            prepared = (await call("etalon_executor_prepare", {"kind": "molcascade", "configuration": {
                "cascade": configured["configuration"], "readout": {"stage_id": "properties",
                    "contract_id": "property/v1", "value_column": "mw"}},
                "endpoint": {"id": "mw", "target": "offline-fixture", "quantity": "molecular_weight",
                             "units": "Da", "cost": 1}}))["executor"]
            await call("etalon_active_create", {"database": database,
                "spec": {"objective": "mw", "budget": 3, "cost_unit": "fixture_quotes",
                         "representation": MOLECULAR_REPRESENTATION, "batch_size": 1},
                "endpoints": [prepared["endpoint"]]})
            registration = {"database": database, "prepared": prepared,
                            "rationale": "Explicit CPU fixture readout, no affinity claim"}
            assert (await call("etalon_executor_register", registration))["registered"] is True
            assert (await call("etalon_executor_register", registration))["registered"] is False
            assert (await call("etalon_executor_list", {"database": database}))["executors"] == {"mw": prepared}

            spec = {"schema": "etalon-workflow/1", "objective": "Prepare and screen three local fixtures",
                "controller": {"mode": "external", "max_calls": 10}, "max_seconds": 120,
                "limits": {"http_requests": 0, "http_bytes": 1_000_000, "cpu_quotes": 1}, "nodes": [
                    acquisition(fixture),
                    {"id": "library", "operation": "data.prepare", "arguments": {
                        "snapshot": {"$ref": "acquire.snapshot"}, "id_field": "fields.id",
                        "smiles_field": "fields.smiles", "min_records": 3}},
                    {"id": "screen", "operation": "screen.run", "arguments": {
                        "config_path": configured["config_path"], "library_path": {"$ref": "library.library_path"},
                        "workers": 1, "devices": ["cpu"]}, "resources": {"cpu_quotes": 1}},
                    {"id": "export", "operation": "screen.export", "arguments": {
                        "workspace": {"$ref": "screen.workspace"}, "run_id": {"$ref": "screen.run_id"}}},
                    {"id": "import", "operation": "campaign.import", "arguments": {
                        "database": database, "snapshot": {"$ref": "library.snapshot"}}}]}
            plan = await call("etalon_workflow_plan", {"spec": spec})
            submission = {"spec": spec, "workspace": workspace, "job_id": "prepare-screen",
                          "plan_id": plan["plan_id"]}
            job = (await call("etalon_workflow_submit", submission))["job"]
            assert job["state"] == "awaiting_decision"
            assert (await call("etalon_workflow_submit", submission))["job"]["id"] == job["id"]
            identity = {"workspace": workspace, "job_id": "prepare-screen"}
            observed = await call("etalon_workflow_observe", identity)
            original_id = observed["observation_id"]
            assert not observed["all_verified"]
            assert [node["id"] for node in observed["ready"]] == ["acquire"]
            proposal = {"observation_id": original_id, "node_id": "acquire", "reason": "Acquire the local fixture first"}
            for invalid in ({**proposal, "node_id": "finish"}, {**proposal, "node_id": "screen"},
                            {**proposal, "observation_id": "stale"}, {**proposal, "shell": "anything"}):
                await call("etalon_workflow_advance", {**identity, "proposal": invalid}, accepted=False)
            unchanged = await call("etalon_workflow_observe", identity)
            assert unchanged["observation_id"] == original_id
            await call("etalon_workflow_advance", {**identity, "proposal": proposal})
            await call("etalon_workflow_advance", {**identity, "proposal": proposal}, accepted=False)
            job = await settled("prepare-screen")
            assert job["state"] == "awaiting_decision", job
            await call("etalon_workflow_advance", {**identity, "proposal": proposal}, accepted=False)
            for expected in ("library", "screen", "export", "import"):
                observed = await call("etalon_workflow_observe", identity)
                assert expected in {node["id"] for node in observed["ready"]}
                await call("etalon_workflow_advance", {**identity, "proposal": {
                    "observation_id": observed["observation_id"], "node_id": expected,
                    "reason": "Use verified dependency artifacts for the next declared step"}})
                job = await settled("prepare-screen")
                assert job["state"] == ("succeeded" if expected == "import" else "awaiting_decision"), job
            assert all(node["state"] == "verified" for node in job["nodes"])
            assert (await call("etalon_workflow_observe", identity))["all_verified"] is True
            assert (await call("etalon_workflow_submit", submission))["job"]["state"] == "succeeded"

            rows = await call("etalon_workflow_artifact", {**identity, "node_id": "library",
                "kind": "snapshot", "member": "library.csv", "limit": 2})
            assert rows["total"] == 3 and rows["returned"] == 2 and rows["next_offset"] == 2
            await call("etalon_workflow_artifact", {**identity, "node_id": "library", "kind": "snapshot",
                "member": "../outside.txt"}, accepted=False)
            stages = await call("etalon_workflow_artifact", {**identity, "node_id": "screen", "member": "/run/stages"})
            artifact = next(stage["artifact_id"] for stage in stages["data"] if stage["stage_id"] == "properties")
            measured = await call("etalon_workflow_artifact", {**identity, "node_id": "screen", "kind": "screen",
                "artifact_id": artifact, "contract_id": "property/v1"})
            # The standardizer removes sodium and preserves the acetate anion's charge.
            assert sorted(row["mw"] for row in measured["data"]) == pytest.approx([46.069, 59.044, 180.159], abs=0.02)

            active_options = {"database": database, "max_rounds": 3, "min_new_admitted": 3, "max_seconds": 120}
            active_plan = await call("etalon_active_execution_plan", active_options)
            assert active_plan["campaign"]["reservation"] == {"campaign:fixture_quotes": 3}
            active_submission = {**active_options, "workspace": workspace, "job_id": "active-cpu",
                                 "plan_id": active_plan["plan_id"]}
            await call("etalon_active_submit", active_submission)
            job = await settled("active-cpu")
            assert job["state"] == "succeeded", job
            assert job["nodes"][0]["outputs"]["new_admitted"] == 3
            observations = await call("etalon_workflow_artifact", {"workspace": workspace, "job_id": "active-cpu",
                "node_id": "active", "member": "/observations", "limit": 1})
            assert observations["total"] == 3 and observations["returned"] == 1 and observations["next_offset"] == 1
            assert observations["data"][0]["admitted"] is True
            assert (await call("etalon_active_submit", active_submission))["job"]["state"] == "succeeded"
            await call("etalon_active_submit", {**active_submission, "max_rounds": 2}, accepted=False)
            active_state = await call("etalon_active_status", {"database": database})
            assert active_state["state"]["admitted"] == 3 and active_state["state"]["balance"]["spent"] == 3

            cancelled_spec = copy.deepcopy(spec)
            cancelled_spec["nodes"] = cancelled_spec["nodes"][:1]
            cancelled_plan = await call("etalon_workflow_plan", {"spec": cancelled_spec})
            await call("etalon_workflow_submit", {"spec": cancelled_spec, "workspace": workspace,
                "job_id": "cancelled", "plan_id": cancelled_plan["plan_id"]})
            stopped = await call("etalon_workflow_cancel", {"workspace": workspace, "job_id": "cancelled",
                "reason": "Stop the unstarted external-controller fixture"})
            assert stopped["job"]["state"] == "cancelled"
            reconciled = await call("etalon_workflow_reconcile", {"workspace": workspace, "job_id": "cancelled",
                "reason": "Inspect without authorizing any resumed work"})
            assert reconciled["job"]["state"] != "succeeded"

    asyncio.run(run())


def cli(*arguments):
    result = subprocess.run([sys.executable, "-m", "etalon", *map(str, arguments)],
                            capture_output=True, text=True, check=False,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=30)
    assert result.stdout.strip(), result.stderr
    return result.returncode, json.loads(result.stdout)


def test_cli_json_configuration_executor_and_external_workflow(tmp_path):
    design_path = tmp_path / "design.json"
    design_path.write_text(json.dumps(design()), encoding="utf-8")
    workspace = tmp_path / "project"
    code, configured = cli("workflow", "config", "--workspace", workspace, "--config", design_path)
    assert code == 0 and Path(configured["config_path"]).is_file()
    prepared_path = tmp_path / "executor.json"
    executor_request = tmp_path / "executor-request.json"
    executor_request.write_text(json.dumps({"kind": "molcascade", "configuration": {
        "cascade": configured["configuration"], "readout": {"stage_id": "properties",
            "contract_id": "property/v1", "value_column": "mw"}}, "endpoint": {"id": "mw",
        "target": "fixture", "quantity": "molecular_weight", "units": "Da", "cost": 1}}), encoding="utf-8")
    code, prepared = cli("executor", "prepare", "--config", executor_request, "--output", prepared_path)
    assert code == 0 and json.loads(prepared_path.read_text()) == prepared
    from etalon.active.schema import CampaignSpec, Endpoint
    from etalon.active.store import CampaignStore

    database = tmp_path / "campaign.sqlite"
    CampaignStore(database).configure(CampaignSpec("mw", 1, "fixture", MOLECULAR_REPRESENTATION),
                                      [Endpoint(**prepared["executor"]["endpoint"])])
    code, registered = cli("executor", "register", "--database", database, "--config", prepared_path,
                           "--rationale", "Explicit CPU fixture")
    assert code == 0 and registered["registered"] is True
    code, listed = cli("executor", "list", "--database", database)
    assert code == 0 and listed["executors"] == {"mw": prepared["executor"]}
    before = prepared_path.read_bytes()
    code, refused = cli("executor", "prepare", "--config", executor_request, "--output", prepared_path)
    assert code == 1 and refused["ok"] is False and prepared_path.read_bytes() == before

    fixture = tmp_path / "catalog.csv"
    fixture.write_text("id,smiles\na,CCO\nb,CCN\nc,CCC\n", encoding="utf-8")
    spec = {"schema": "etalon-workflow/1", "objective": "Await an external controller decision",
            "nodes": [acquisition(fixture)], "limits": {"http_requests": 0, "http_bytes": 1_000_000},
            "controller": {"mode": "external"}}
    config_path, plan_path = tmp_path / "workflow.json", tmp_path / "plan.json"
    config_path.write_text(json.dumps(spec), encoding="utf-8")
    code, plan = cli("workflow", "plan", "--config", config_path, "--output", plan_path)
    assert code == 0 and plan["plan_id"]
    code, submitted = cli("workflow", "submit", "--config", plan_path, "--workspace", workspace,
                          "--job-id", "external", "--plan-id", plan["plan_id"])
    assert code == 0 and submitted["job"]["state"] == "awaiting_decision"
    identity = ["--workspace", workspace, "--job-id", "external"]
    code, observed = cli("workflow", "observe", *identity)
    assert code == 0 and observed["ready"][0]["id"] == "acquire"
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(json.dumps({"observation_id": observed["observation_id"],
                                       "node_id": "finish", "reason": "Unverified finish claim"}), encoding="utf-8")
    code, rejected = cli("workflow", "advance", *identity, "--proposal", proposal_path)
    assert code == 1 and rejected["ok"] is False
    code, stopped = cli("workflow", "cancel", *identity, "--reason", "Cancel the unstarted fixture")
    assert code == 0 and stopped["job"]["state"] == "cancelled"
    code, inspected = cli("workflow", "status", *identity)
    assert code == 0 and inspected["job"]["state"] == "cancelled"


@pytest.mark.parametrize("payload", ['{"node_id":"a","node_id":"b"}', '{"nested":{"id":1,"id":2}}',
                                     '{"cost":NaN}', '{"cost":Infinity}', '{"cost":1e309}', '[]'])
def test_cli_rejects_ambiguous_or_nonfinite_json(tmp_path, payload):
    source = tmp_path / "input.json"
    source.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        _read(source)


def test_runtime_tools_declare_dispatch_costs_and_nonblocking_preparation():
    import inspect

    from etalon.mcp import runtime

    class Collector:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def register(function):
                self.tools[function.__name__] = function
                return function
            return register

    collector = Collector()
    runtime.register(collector)
    classification = costs()
    for name in ("etalon_workflow_submit", "etalon_workflow_advance", "etalon_active_submit",
                 "etalon_workflow_reconcile"):
        assert classification[name] == "spends"
    for name in ("etalon_executor_prepare", "etalon_screen_configure", "etalon_workflow_artifact",
                 "etalon_workflow_reconcile"):
        assert inspect.iscoroutinefunction(collector.tools[name])
    for name in ("etalon_workflow_status", "etalon_workflow_observe", "etalon_executor_list"):
        assert classification[name] == "free"
        refused = json.loads(collector.tools[name](**({"database": "relative.sqlite"} if name.endswith("list")
                             else {"workspace": "relative", "job_id": "job"})))
        assert refused["ok"] is False and "absolute" in refused["error"]["message"]
