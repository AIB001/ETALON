"""Scoped discovery retains real versions without reading unrelated bundles."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from etalon import skills
from etalon.mcp import host as host_tools
from etalon.mcp.server import costs, register_tools
from etalon.runtime.operations import describe


def tools():
    found = {}

    class Collector:
        def tool(self):
            def add(function):
                found[function.__name__] = function
                return function
            return add

    register_tools(Collector())
    return found


def test_zero_argument_catalog_and_capabilities_retain_legacy_shape():
    rows = [skills.snapshot(slug)[0] for slug in skills.WORKFLOWS]
    role = skills.ROLE_PATH.read_bytes()
    expected = {
        "schema": "etalon-capability-catalog/1", "skills": rows,
        "catalog_sha256": skills.digest({row["slug"]: row["manifest_sha256"] for row in rows}),
        "usage_method": {"kind": "usage_method", "name": "etalon-host-guidance",
                         "sha256": skills.sha256(role), "bytes": len(role), "uri": "etalon://host"},
        "load_evidence": "server-read-bytes; host discovery and invocation are not observed",
    }
    assert skills.catalog() == expected
    actual = json.loads(tools()["etalon_capabilities"]())
    assert actual == {"ok": True, **expected, "tools": costs(), "operations": describe(),
                      "runtime": {"python": sys.executable, "module": str(Path(host_tools.__file__).resolve()),
                                  "durable_workers": "Linux /proc and POSIX locks required"}}


def test_subset_scans_only_requested_bundle_and_expands_same_version(monkeypatch):
    real = skills.snapshot
    calls = []

    def selected_only(slug, **kwargs):
        calls.append(slug)
        assert slug == "campaign", "unrelated bundle was scanned"
        return real(slug, **kwargs)

    monkeypatch.setattr(skills, "snapshot", selected_only)
    compact = skills.catalog(detail="summary", skills=["campaign"])
    expanded = skills.catalog(skills=["campaign"])
    assert calls == ["campaign", "campaign"]
    assert compact["catalog_sha256"] == expanded["catalog_sha256"]
    assert compact["skills"][0]["manifest_sha256"] == expanded["skills"][0]["manifest_sha256"]
    assert "files" not in compact["skills"][0]
    assert expanded["skills"][0]["files"]
    assert len(json.dumps(compact)) < len(json.dumps(expanded))


def test_empty_skills_do_not_touch_any_bundle_or_require_it_installed(monkeypatch):
    monkeypatch.setattr(skills, "snapshot", lambda *args, **kwargs: pytest.fail("unexpected bundle read"))
    result = json.loads(tools()["etalon_capabilities"](
        detail="summary", skills=[], tools=["etalon_skill_read"], operations=[]))
    assert result["ok"] and result["skills"] == [] and result["operations"] == {}
    assert list(result["tools"]) == ["etalon_skill_read"]
    assert result["tools"]["etalon_skill_read"]["cost"] == "free"
    assert result["tools"]["etalon_skill_read"]["description"]


def test_scoped_tools_operations_expand_without_manifest_payload():
    call = tools()["etalon_capabilities"]
    params = dict(skills=[], tools=["etalon_action_result"], operations=["active.evaluate"])
    compact, expanded = (json.loads(call(detail=detail, **params)) for detail in ("summary", "full"))
    assert compact["ok"] and expanded["ok"]
    assert compact["tools"]["etalon_action_result"]["cost"] == expanded["tools"]["etalon_action_result"]
    assert compact["operations"] == expanded["operations"] == {"active.evaluate": describe()["active.evaluate"]}
    assert compact["scope"] == params


@pytest.mark.parametrize("selector,value", [("skills", ["absent"]), ("skills", "campaign"),
    ("skills", ["campaign", "campaign"]), ("tools", ["absent"]),
    ("operations", ["absent"]), ("tools", [1]), ("detail", "tiny")])
def test_invalid_selection_refuses_instead_of_falling_back(selector, value):
    parameters = dict(detail="summary", skills=[], tools=[], operations=[])
    parameters[selector] = value
    result = json.loads(tools()["etalon_capabilities"](**parameters))
    assert result["ok"] is False
    assert result["error"]["retryable"] is False
    assert "skills" not in result and "tools" not in result


def test_summary_digest_tracks_supporting_content_and_open_bundle_still_works(tmp_path, monkeypatch):
    manifest, contents = skills.snapshot("campaign")
    root = tmp_path / "bundle"
    for member, content in contents.items():
        path = root / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    monkeypatch.setattr(skills, "bundle_root", lambda slug: root)
    before = skills.catalog(detail="summary", skills=["campaign"])
    (root / "references/local-note.md").write_text("New local method, unproven.", encoding="utf-8")
    after = skills.catalog(detail="summary", skills=["campaign"])
    assert before["skills"][0]["manifest_sha256"] == manifest["manifest_sha256"]
    assert after["catalog_sha256"] != before["catalog_sha256"]
    external, _ = skills.snapshot("local-candidate", bundle_ref={"root": str(root)})
    # A catalog selector is not a method authorization boundary.
    read = skills.read_member("local-candidate", "references/local-note.md",
        bundle_ref={"root": str(root), "manifest_sha256": external["manifest_sha256"]})
    assert read["content"] == "New local method, unproven."


def test_cli_summary_uses_identical_scoped_version(tmp_path):
    source = str(Path(skills.__file__).resolve().parent.parent)
    result = subprocess.run([sys.executable, "-S", "-m", "etalon.host", "catalog",
                             "--detail", "summary", "--skills", "campaign"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": source, "PYTHONUTF8": "1"},
        check=True, capture_output=True, encoding="utf-8", timeout=30)
    report = json.loads(result.stdout)
    assert report["ok"] and report["scope"] == {"skills": ["campaign"]}
    assert report["skills"] == skills.catalog(detail="summary", skills=["campaign"])["skills"]
