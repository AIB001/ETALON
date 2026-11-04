"""Real local storage and CLI/MCP handlers; software fixtures, no science/server."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from etalon.active import CampaignStore, CampaignSpec, Endpoint
from etalon.research import ResearchJournal
from etalon.research.notes import read_entry, save_progress


class Collector:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def add(function):
            self.tools[function.__name__] = function
            return function
        return add


def _json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _cli(*args):
    completed = subprocess.run([sys.executable, "-m", "etalon", "research", *map(str, args)],
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    body = json.loads(completed.stdout)
    assert body.get("ok", True), body
    return body


def _mcp(collector, name, **kwargs):
    result = json.loads(collector.tools[name](**kwargs))
    assert result["ok"], result
    return result


def _snapshot(entry, database):
    # Logical SQLite content and entry bytes: read-time journal-mode housekeeping
    # is not a research write, but inserted/changed records always alter this hash.
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        content = "\n".join(db.iterdump()).encode("utf-8")
    return entry.read_bytes(), hashlib.sha256(content).hexdigest()


@pytest.fixture
def surface(tmp_path):
    from etalon.mcp.evidence import register as evidence_register
    from etalon.mcp.methods import register as methods_register
    collector = Collector()
    evidence_register(collector)
    methods_register(collector)
    database = tmp_path / "research.sqlite"
    store = CampaignStore(database)
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    entry = tmp_path / "ENTRY.json"
    save_progress(ResearchJournal(store), entry, {"task_id": "surface-fixture", "branch_id": "main",
        "question": "Native local API parity", "summary": "Temporary software fixture", "conditions": {"target": "PB"}})
    marker = tmp_path / "SHOULD_NOT_EXECUTE"
    script = tmp_path / "method.py"
    script.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n", encoding="utf-8")
    definitions = {"distance": "a" * 64}
    kernel = {"part_id": "kernel", "conditions": {"format": "coordinates"}, "requires": [],
        "irrelevant": ["target"], "available_at": "planning", "dependencies": [], "definitions": definitions}
    residues = {**kernel, "part_id": "residues", "conditions": {"target": "SND1", "format": "coordinates"}, "irrelevant": []}
    descriptor = {"arguments": {"method_id": "geometry/fixture", "command": [sys.executable, str(script)],
        "files": [str(script)], "inputs": {}}, "summary": "Geometry fixture with target-specific residues",
        "conditions": {"target": "SND1"}, "family": "geometry", "parts": [kernel, residues], "definitions": definitions}
    published = _mcp(collector, "etalon_method_publish", entry=str(entry), resource_id="geometry", **descriptor)
    query = {"conditions": {"target": "PB", "format": "coordinates"},
             "available_stages": ["planning"], "definitions": definitions}
    return {"collector": collector, "entry": entry, "database": database, "marker": marker,
            "published": published, "descriptor": descriptor, "query": query, "directory": tmp_path}


def _feedback(surface, usage_id="same-use", outcome="negative"):
    return {"resource_id": "geometry", "usage_id": usage_id,
        "resource_version": surface["published"]["resource_version"], "part_ids": ["kernel"],
        "conditions": {"target": "PB"}, "outcome": outcome,
        "summary": "Explicit software observation, no scientific effect", "cost": {"wall_second": None}}


def test_registered_mcp_and_new_cli_process_share_partial_selection_without_mutation(surface):
    collector, entry = surface["collector"], surface["entry"]
    selected = _mcp(collector, "etalon_research_context", entry=str(entry), view="resources",
        view_options={"families": ["geometry"], "query": surface["query"]})
    assert len(selected["items"]) == 1
    row = selected["items"][0]
    assert row["selection"]["disposition"] == "adapt"
    assert [part["disposition"] for part in row["selection"]["parts"]] == ["direct-use", "adapt"]
    assert row["definitions"] == surface["descriptor"]["definitions"]
    pin = _json(surface["directory"] / "selected-entry.json", row["content_ref"])
    query_path = _json(surface["directory"] / "query.json", surface["query"])
    _mcp(collector, "etalon_research_progress", entry=str(entry), patch={"summary": "Later unrelated progress"})
    before = _snapshot(entry, surface["database"])
    resolved = _cli("method", "--entry", pin, "--pointer", row["pointer"], "--query", query_path)
    assert resolved["record_id"] == row["content_ref"]["record_id"]
    assert resolved["resource_version"] == surface["published"]["resource_version"]
    assert resolved["selection"] == row["selection"]
    _mcp(collector, "etalon_method_resolve", entry=row["content_ref"], pointer=row["pointer"], query=surface["query"])
    cli_catalog = _cli("resources", "--entry", entry, "--family", "geometry", "--query", query_path)
    assert [r["resource_id"] for r in cli_catalog["items"]] == ["geometry"]
    assert _snapshot(entry, surface["database"]) == before
    assert not surface["marker"].exists()


def test_same_feedback_replays_across_mcp_and_cli_with_nested_and_outer_evidence(surface):
    collector, entry, directory = surface["collector"], surface["entry"], surface["directory"]
    observed = _json(directory / "observed.json", {"outcome": "negative", "fixture": True})
    context = _json(directory / "context.json", {"missing": "scientific evaluation", "fixture": True})
    contrary = _json(directory / "contrary.json", {"counterexample": True, "fixture": True})
    payload = {**_feedback(surface), "branch_id": "main", "evidence": [str(observed)],
               "counterevidence": [str(contrary)]}
    first = _mcp(collector, "etalon_research_progress", entry=str(entry), patch={}, resource_feedback=payload,
        evidence=[str(observed), str(context)], counterevidence=[str(contrary)])
    _mcp(collector, "etalon_research_progress", entry=str(entry), patch={"summary": "Later facts remain current"})
    replay = _mcp(collector, "etalon_research_progress", entry=str(entry), patch={}, resource_feedback=payload,
        evidence=[str(observed), str(context)], counterevidence=[str(contrary)])
    assert replay["replayed"] and replay["record_id"] == first["record_id"]
    payload_path = _json(directory / "feedback.json", payload)
    before = _snapshot(entry, surface["database"])
    cli_replay = _cli("progress", "--entry", entry, "--resource-feedback", payload_path,
        "--evidence", context, "--evidence", observed, "--counterevidence", contrary)
    assert cli_replay["replayed"] and cli_replay["record_id"] == first["record_id"]
    assert _snapshot(entry, surface["database"]) == before
    feedback = read_entry(entry, verify_originals=False)["note"]["resources"]["feedback:geometry"]
    assert feedback["feedback"]["outcome_counts"] == {"negative": 1}
    assert len(feedback["last_receipt"]["evidence"]) == 2
    assert len(feedback["last_receipt"]["counterevidence"]) == 1


def test_family_discovery_keeps_earlier_negative_feedback_after_cli_method_replacement(surface):
    collector, entry, directory = surface["collector"], surface["entry"], surface["directory"]
    _mcp(collector, "etalon_research_progress", entry=str(entry), patch={}, resource_feedback=_feedback(surface))
    replacement = {**surface["descriptor"], "family": "geometry-v2",
        "summary": "New current geometry fixture", "parts": [surface["descriptor"]["parts"][0]]}
    descriptor_file = _json(directory / "replacement.json", replacement)
    published = _cli("method-save", "--entry", entry, "--resource-id", "geometry", "--descriptor", descriptor_file)
    failed = {**_feedback(surface, "new-use", "failed"), "resource_version": published["resource_version"]}
    failed_file = _json(directory / "failed.json", failed)
    _cli("progress", "--entry", entry, "--resource-feedback", failed_file)
    old_family = _mcp(collector, "etalon_research_context", entry=str(entry), view="resources",
        view_options={"families": ["geometry"], "query": surface["query"]})
    assert [r["resource_id"] for r in old_family["items"]] == ["feedback:geometry"]
    assert old_family["items"][0]["feedback"]["outcome_counts"] == {"negative": 1, "failed": 1}
    assert old_family["items"][0]["selection"]["disposition"] == "reference"
    new_family = _cli("resources", "--entry", entry, "--family", "geometry-v2")
    assert {r["resource_id"] for r in new_family["items"]} == {"geometry", "feedback:geometry"}
    resolved = _mcp(collector, "etalon_method_resolve", entry=str(entry), pointer="/resources/geometry")
    assert resolved["description"]["definitions"] == replacement["definitions"]
    assert resolved["description"]["parts"] == replacement["parts"]
    assert resolved["description"]["family"] == "geometry-v2"
