"""Executable example invariants; synthetic engineering, no native adoption claim."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import closing

from etalon import skills
from etalon.active import CampaignStore
from etalon.research import ResearchJournal
from etalon.boundary.prism_v1 import file_identity


def test_revision_preserves_versions_partial_use_and_bounded_event_stop(tmp_path):
    path = Path(__file__).parents[1] / "examples" / "skill_improvement.py"
    spec = importlib.util.spec_from_file_location("skill_improvement_example", path)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    report = example.run(tmp_path / "trial")
    journal = ResearchJournal(CampaignStore(report["database"]))
    versions = report["versions"]
    old = skills.verify_snapshot(report["content_refs"]["old_s"])
    new = skills.verify_snapshot(report["content_refs"]["new_s"])
    assert old["manifest_sha256"] != new["manifest_sha256"]
    assert journal.get(versions["new_s"])["parents"] == [versions["old_s"]]
    assert journal.get(versions["new_usage"])["parents"] == [versions["old_usage"]]
    use = journal.get(report["use"])
    assert use["usage_method_ref"] == versions["new_usage"]
    assert use["selected_methods"][0]["scope"] == "exact reference pointer only"
    assert use["basis"] == "host_reported"
    assert journal.get(report["stop"])["selected_methods"] == []
    assert journal.get(report["evaluation"])["assessment_role"] == "diagnostic"
    assert len(report["hook"]["handled"]) == 1
    assert report["replay"]["handled"] == []
    assert len(report["decisions"]) == 1
    assert report["observations"] == 0 and report["independent_benefit"] == "not_run"
    assert not report["default_changed"]
    assert report["after_notes"]["handled"] == []
    assert journal.get(report["rollback"])["selected_methods"][0]["method_version_ref"] == versions["old_s"]
    for kind, revision_id in report["revisions"].items():
        artifact = journal.get(revision_id)
        payload = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
        assert payload["producer_method_ref"]["record_id"] == report["generator_versions"]["g0"]
        assert payload["producer_use_ref"]["record_id"] == report["producer_use"]
        if kind == "no_change":
            assert payload["candidate_version_refs"] == []
        else:
            candidate = journal.get(payload["candidate_version_refs"][0]["record_id"])
            assert candidate["parents"] == [payload["base_method_ref"]["record_id"]]


def test_ordinary_revision_published_journal_is_discoverable_in_new_process(tmp_path):
    """Reader receives only workspace/topic, never expected IDs or a fabricated projection."""
    path = Path(__file__).parents[1] / "examples" / "skill_improvement.py"
    spec = importlib.util.spec_from_file_location("skill_improvement_publish_example", path)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    report = example.run(tmp_path / "trial")
    snapshot = tmp_path / "published.sqlite"
    with closing(sqlite3.connect(report["database"])) as source, closing(sqlite3.connect(snapshot)) as target:
        source.backup(target)
    workspace = tmp_path / "workspace.json"
    workspace.write_text(json.dumps({
        "schema": "etalon-evidence-workspace/1", "workspace_id": "ordinary-revision-publish",
        "sources": [{"source_id": "revision-example", "kind": "journal", "location": snapshot.name,
                     "identity": {"kind": "snapshot_sha256", "value": file_identity(snapshot)["sha256"]}}],
    }), encoding="utf-8")
    before = file_identity(snapshot)
    reader = '''
import json, sys
from etalon.research.evidence import query_evidence, read_evidence
workspace, topic = sys.argv[1:]
query = query_evidence(workspace, query=topic, limit=50)
rows = [{"ref": item["ref"], "record": read_evidence(workspace, item["ref"], view="record"),
         "lineage": read_evidence(workspace, item["ref"], view="lineage", limit=50)}
        for item in query["items"]]
print(json.dumps({"query": query, "rows": rows}))
'''
    child = subprocess.run([sys.executable, "-c", reader, str(workspace), "Fixture candidate"],
                           cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", check=True)
    result = json.loads(child.stdout)
    by_id = {row["ref"]["record_id"]: row for row in result["rows"]}
    journal = ResearchJournal(CampaignStore(report["database"]))
    for kind, base, candidate in (("S", report["versions"]["old_s"], report["versions"]["new_s"]),
                                  ("U", report["versions"]["old_usage"], report["versions"]["new_usage"]),
                                  ("G", report["generator_versions"]["g0"], report["generator_versions"]["g1"])):
        identifier = report["revisions"][kind]
        assert identifier in by_id
        row = by_id[identifier]
        assert row["record"]["record"] == journal.get(identifier)
        linked = {ref["record_id"] for ref in row["lineage"]["items"]}
        assert {base, candidate, report["generator_versions"]["g0"], report["producer_use"]} <= linked
        assert all(ref["source_id"] == "revision-example" for ref in row["lineage"]["items"])
    assert file_identity(snapshot) == before
