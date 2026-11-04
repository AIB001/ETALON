"""Actual CLI and registered MCP reads share the same immutable feedback page."""
import json
import subprocess
import sys

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research import ResearchJournal
from etalon.research.notes import patch_progress, save_progress
from etalon.research.resource_selection import record_resource_feedback
from etalon.research.saved_methods import publish_method


def test_feedback_pages_cross_cli_mcp_and_remain_pinned(tmp_path):
    store = CampaignStore(tmp_path / "journal.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "fixture", "recorded", "count", "fixture/1", 0, queryable=False)])
    entry = tmp_path / "entry.json"
    save_progress(ResearchJournal(store), entry, {"task_id": "fixture", "branch_id": "main",
        "question": "delivery", "summary": "software fixture only"})
    script = tmp_path / "method.py"
    script.write_text("raise AssertionError('this reader must never execute')\n")
    published = publish_method(entry, "rank", {"method_id": "fixture", "command": [sys.executable, str(script)],
        "files": [str(script)], "inputs": {}}, summary="fixture", conditions={"target": "A"})
    record_resource_feedback(entry, "rank", usage_id="negative", resource_version=published["resource_version"],
        part_ids=["whole"], conditions={"target": "A"}, outcome="negative", summary="known failure", cost={"wall_second": 7})
    class Collector:
        def __init__(self):
            self.tools = {}
        def tool(self):
            def add(function):
                self.tools[function.__name__] = function
                return function
            return add
    from etalon.mcp.methods import register
    collector = Collector()
    register(collector)
    page = json.loads(collector.tools["etalon_resource_feedback"](entry=str(entry), resource_id="rank"))
    assert page["ok"], page
    assert "known failure" in json.dumps(page)
    pin = tmp_path / "pin.json"
    pin.write_text(json.dumps(page["content_ref"]))
    patch_progress(entry, {"summary": "head moved"})
    result = subprocess.run([sys.executable, "-m", "etalon", "research", "feedback", "--entry", str(pin),
        "--resource-id", "rank"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    cli = json.loads(result.stdout)
    assert cli["record_id"] == page["record_id"]
    assert cli["cases"] == page["cases"]
    assert cli["coverage"]["counts_scope"] == "returned_cases"
