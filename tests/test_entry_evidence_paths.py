"""Evidence attachments use the caller's entry directory, including locators."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from mcp.server.fastmcp import FastMCP

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research import ResearchJournal
from etalon.research.notes import entry_value, original, patch_progress, read_entry, save_progress
from etalon.research.saved_methods import publish_method


@pytest.fixture
def project(tmp_path):
    work, internal, elsewhere = tmp_path / "work", tmp_path / "work" / "handoff", tmp_path / "server-cwd"
    for directory in (work, internal, elsewhere):
        (directory / "work_out").mkdir(parents=True, exist_ok=True)
        for name in ("support.json", "contrary.json"):
            (directory / "work_out" / name).write_text(json.dumps({"fixture_origin": str(directory), "name": name}))
    store = CampaignStore(internal / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    entry = internal / "ENTRY.json"
    external = tmp_path / "absolute.json"
    external.write_text('{"fixture":"already pinned"}')
    pinned = original(external)
    resource = {"kind": "experience", "summary": "Uninterpreted resource fields stay unchanged",
                "relative_text": "work_out/unrelated.json", "historical_pin": pinned}
    save_progress(ResearchJournal(store), entry, {"task_id": "path-fixture", "branch_id": "main",
        "question": "Which file did the caller attach?", "summary": "Software fixture",
        "evidence": [pinned], "resources": {"untouched": resource}})
    locator = work / "CURRENT.json"
    locator.write_text(json.dumps({"entry": "handoff/ENTRY.json"}))
    return dict(work=work, internal=internal, elsewhere=elsewhere, entry=entry, locator=locator,
                external=external, pinned=pinned, resource=resource)


def mcp_progress(**arguments):
    from etalon.mcp.evidence import register
    server = FastMCP("path-fixture")
    register(server)
    response = asyncio.run(server.call_tool("etalon_research_progress", arguments))
    blocks = response[0] if isinstance(response, tuple) else response
    result = json.loads(blocks[0].text)
    assert result["ok"], result
    return result


def cli_progress(project, mode, payload, *extra):
    payload_file = project["elsewhere"] / (mode + ".json")
    payload_file.write_text(json.dumps(payload))
    import etalon
    source = str(Path(etalon.__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, "-m", "etalon", "research", "progress",
        "--entry", str(project["locator"]), "--" + mode, str(payload_file), *map(str, extra)],
        cwd=project["elsewhere"], env={**os.environ, "PYTHONPATH": source},
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


@pytest.mark.parametrize("address", ["entry", "locator"])
def test_native_mcp_uses_supplied_entry_parent_and_preserves_existing_pins(project, monkeypatch, address):
    before = project["locator"].read_bytes()
    monkeypatch.chdir(project["elsewhere"])
    mcp_progress(entry=str(project[address]), patch={"summary": "Attached real fixture files"},
        evidence=["work_out/support.json", str(project["external"])],
        counterevidence=["work_out/contrary.json"])
    base = project[address].parent
    note = read_entry(project["locator"])["note"]
    assert note["evidence"] == [project["pinned"], original(base / "work_out/support.json")]
    assert note["counterevidence"] == [original(base / "work_out/contrary.json")]
    assert note["resources"]["untouched"] == project["resource"]
    assert project["locator"].read_bytes() == before


@pytest.mark.parametrize("mode", ["patch", "note"])
def test_cli_patch_and_complete_note_resolve_beside_locator_not_json_payload(project, mode):
    payload = {"summary": "CLI attached files"} if mode == "patch" else read_entry(project["locator"])["note"]
    payload["summary"] = "CLI attached files"
    cli_progress(project, mode, payload, "--evidence", "work_out/support.json",
                 "--counterevidence", "work_out/contrary.json", "--evidence", project["external"])
    note = read_entry(project["locator"])["note"]
    assert note["evidence"] == [project["pinned"], original(project["work"] / "work_out/support.json")]
    assert note["counterevidence"] == [original(project["work"] / "work_out/contrary.json")]
    assert note["resources"]["untouched"] == project["resource"]


@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_feedback_nested_and_outer_paths_use_locator_without_retargeting_old_method(project, monkeypatch, surface):
    entry = project["locator"]
    script = project["work"] / "method.py"
    script.write_text("raise AssertionError('must not execute')\n")
    arguments = {"method_id": "path-fixture", "command": [sys.executable, str(script)],
                 "files": [str(script)], "inputs": {}}
    first = publish_method(entry, "method", arguments, summary="First version", conditions={})
    old_entry = entry_value(entry)
    publish_method(entry, "method", arguments, summary="Second version", conditions={})
    feedback = dict(resource_id="method", usage_id="old-version-observation", resource_version=first["resource_version"],
        resource_entry=old_entry, part_ids=["whole"], conditions={}, outcome="unknown", summary="Software fixture",
        evidence=["work_out/support.json"], counterevidence=["work_out/contrary.json"])
    monkeypatch.chdir(project["elsewhere"])
    if surface == "mcp":
        mcp_progress(entry=str(entry), patch={}, resource_feedback=feedback,
                     evidence=[str(project["work"] / "work_out/support.json"), str(project["external"])])
    else:
        cli_progress(project, "resource-feedback", feedback, "--evidence", "work_out/support.json",
                     "--evidence", project["external"])
    descriptor = read_entry(entry)["note"]["resources"]["feedback:method"]
    receipt = descriptor["last_receipt"]
    assert receipt["subject"]["version"] == first["resource_version"]
    assert receipt["resource_entry"]["record_id"] == old_entry["record_id"]
    assert {ref["path"] for ref in receipt["evidence"]} == {
        str(project["work"] / "work_out/support.json"), str(project["external"])}
    assert receipt["counterevidence"] == [original(project["work"] / "work_out/contrary.json")]


def test_sdk_does_not_rebase_pins_embedded_in_patch_or_resources(project, monkeypatch):
    monkeypatch.chdir(project["elsewhere"])
    patch_progress(project["locator"], {"evidence": [project["pinned"]]},
        evidence=(path for path in ["work_out/support.json"]), resources={"untouched": project["resource"]})
    note = read_entry(project["locator"])["note"]
    assert note["evidence"] == [project["pinned"], original(project["work"] / "work_out/support.json")]
    assert note["resources"]["untouched"] == project["resource"]


def test_advice_report_embedded_refs_and_note_attachments_agree_for_locator(project, monkeypatch):
    from etalon.research.advice import advice
    prepared = advice(project["locator"], "prepare", "fixture", "v1", {
        "question": "Inspect the fixture", "brief": {"observation": "fixture"}, "execution_scope": {"work": "read only"}})
    advisor_locator = project["work"] / "ADVISOR.json"
    advisor_locator.write_text(json.dumps({"entry": prepared["advisor_entry"]}))
    monkeypatch.chdir(project["elsewhere"])
    advice(advisor_locator, "report", "fixture", "v1", {"outcome": "suggestion", "summary": "Fixture",
        "evidence": ["work_out/support.json"], "counterevidence": ["work_out/contrary.json"]})
    note = read_entry(advisor_locator)["note"]
    assert note["advice_report"]["evidence"] == [original(project["work"] / "work_out/support.json")]
    assert note["advice_report"]["evidence"][0] in note["evidence"]
    assert note["advice_report"]["counterevidence"][0] in note["counterevidence"]
