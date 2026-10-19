"""Host adapters preserve complete capability versions and use the real MCP surface."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from etalon import host, skills
from etalon.mcp.server import costs


@pytest.mark.parametrize("defect", ["unclassified", "duplicate"])
def test_cost_index_rejects_tools_without_an_unambiguous_classification(monkeypatch, defect):
    from etalon.mcp import host as host_tools
    from etalon.mcp._common import Cost, tool

    def register(server):
        def unknown():
            return "{}"

        if defect == "duplicate":
            unknown.__name__ = "etalon_plan_campaign"
            server.tool()(tool(Cost.FREE)(unknown))
        else:
            server.tool()(unknown)

    monkeypatch.setattr(host_tools, "register", register)
    with pytest.raises(ValueError, match="duplicate|classification"):
        costs()


@pytest.mark.parametrize("consumer", ["codex", "claude"])
def test_export_contains_identical_bundles_and_refuses_existing_projects(tmp_path, consumer):
    target = tmp_path / consumer
    report = host.export(target, consumer)
    assert report["catalog_sha256"] == skills.catalog()["catalog_sha256"]
    assert host.verify(target)["export_matches_manifest"]
    assert host.verify(target)["matches_current_source"]
    directory = ".agents/skills" if consumer == "codex" else ".claude/skills"
    for slug, name in skills.WORKFLOWS.items():
        manifest, contents = skills.snapshot(slug)
        assert manifest in report["skills"]
        for member, expected in contents.items():
            assert (target / directory / name / member).read_bytes() == expected
    role = target / ("AGENTS.md" if consumer == "codex" else "CLAUDE.md")
    role.write_text("existing user guidance", encoding="utf-8")
    with pytest.raises(FileExistsError):
        host.export(target, consumer)
    assert role.read_text() == "existing user guidance"


def test_export_audit_distinguishes_changed_missing_and_added_members(tmp_path):
    target = tmp_path / "project"
    host.export(target)
    root = target / ".agents/skills/etalon-campaign"
    (root / "SKILL.md").write_text("modified", encoding="utf-8")
    (root / "references/legacy-workflow.md").unlink()
    (root / "extra.py").write_text("extra", encoding="utf-8")
    result = host.verify(target)
    assert not result["export_matches_manifest"]
    assert result["matches_current_source"]  # Source did not change; the installation did.
    assert result["changed"] == [".agents/skills/etalon-campaign/SKILL.md"]
    assert result["missing"] == [".agents/skills/etalon-campaign/references/legacy-workflow.md"]
    assert result["unexpected"] == [".agents/skills/etalon-campaign/extra.py"]


def test_skill_and_usage_method_versions_change_independently(tmp_path, monkeypatch):
    original_root = skills.bundle_root
    manifest, contents = skills.snapshot("campaign")
    root = tmp_path / "skill"
    for name, data in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setattr(skills, "bundle_root", lambda slug: root if slug == "campaign" else original_root(slug))
    baseline = skills.catalog()
    script = root / "scripts/inspect.py"
    script.parent.mkdir()
    script.write_text("print('fixture')\n", encoding="utf-8")
    changed = skills.catalog()
    assert changed["catalog_sha256"] != baseline["catalog_sha256"]
    assert changed["usage_method"] == baseline["usage_method"]
    script.unlink()
    assert skills.snapshot("campaign")[0] == manifest
    role = tmp_path / "ROLE.md"
    role.write_bytes(skills.ROLE_PATH.read_bytes() + b"\nChanged usage method.\n")
    monkeypatch.setattr(skills, "ROLE_PATH", role)
    usage_changed = skills.catalog()
    assert usage_changed["catalog_sha256"] == baseline["catalog_sha256"]
    assert usage_changed["usage_method"] != baseline["usage_method"]


@pytest.mark.parametrize("member", ["../HOST.md", "/etc/passwd", "references/../SKILL.md", "references\\legacy-workflow.md"])
def test_skill_reads_refuse_path_traversal(member):
    with pytest.raises(ValueError):
        skills.read_member("campaign", member)


def test_manifest_cannot_hide_a_removed_reference(tmp_path):
    target = tmp_path / "project"
    host.export(target)
    path = target / host.MANIFEST
    value = json.loads(path.read_text(encoding="utf-8"))
    name = ".agents/skills/etalon-campaign/references/legacy-workflow.md"
    del value["files"][name]
    (target / name).unlink()
    path.write_text(json.dumps(value), encoding="utf-8")
    assert not host.verify(target)["matches_current_source"]
    value["files"]["../outside"] = {"bytes": 0, "sha256": skills.sha256(b"")}
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="relative"):
        host.verify(target)


def test_wheel_contains_complete_bundles_without_checkout_or_site_packages(tmp_path):
    pytest.importorskip("hatchling")
    repository = Path(__file__).resolve().parents[1]
    distribution = tmp_path / "dist"
    subprocess.run([sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(distribution)],
                   cwd=repository, check=True, capture_output=True, timeout=60)
    isolated = tmp_path / "installed"
    with zipfile.ZipFile(next(distribution.glob("*.whl"))) as archive:
        archive.extractall(isolated)
    environment = {**os.environ, "PYTHONPATH": str(isolated), "PYTHONUTF8": "1"}
    # -S excludes editable-install .pth files and all site packages. The catalog
    # must work on the standard library and load references out of the wheel.
    result = subprocess.run([sys.executable, "-S", "-m", "etalon.host", "catalog"],
                            env=environment, cwd=tmp_path, check=True, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    packaged = json.loads(result.stdout)
    current = skills.catalog()
    assert packaged["catalog_sha256"] == current["catalog_sha256"]
    assert packaged["skills"] == current["skills"]
    assert packaged["usage_method"] == current["usage_method"]
    assert packaged["source"]["pythonpath"] == str(isolated)
    target = tmp_path / "wheel-project"
    subprocess.run([sys.executable, "-S", "-m", "etalon.host", "export", "--target", str(target)],
                   env=environment, cwd=tmp_path, check=True, capture_output=True, timeout=30)
    result = subprocess.run([sys.executable, "-S", "-m", "etalon.host", "verify", "--target", str(target)],
                            env=environment, cwd=tmp_path, check=True, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert json.loads(result.stdout)["ok"]


@pytest.mark.parametrize("consumer", ["codex", "claude"])
def test_exported_launch_configuration_serves_real_stdio_from_another_cwd(tmp_path, consumer):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    target = tmp_path / consumer
    manifest = host.export(target, consumer)
    if consumer == "codex":
        config = tomllib.loads((target / ".codex/config.toml").read_text())["mcp_servers"]["etalon"]
    else:
        config = json.loads((target / ".mcp.json").read_text())["mcpServers"]["etalon"]
    assert config["command"] == str(Path(sys.executable).absolute())

    async def exercise():
        params = StdioServerParameters(command=config["command"], args=config["args"],
                                       env=config["env"], cwd=str(target))
        async with (stdio_client(params) as (read, write), ClientSession(read, write) as session):
            await session.initialize()
            listed = await session.list_tools()
            assert {row.name for row in listed.tools} == set(costs())
            assert "etalon_sweep_emit" in costs()
            response = await session.call_tool("etalon_capabilities", {})
            assert not response.isError
            report = json.loads(response.content[0].text)
            assert report["ok"] and report["tools"] == costs()
            assert report["catalog_sha256"] == manifest["catalog_sha256"]
            assert report["runtime"]["python"] == config["command"]
            resource = await session.read_resource("etalon://skills")
            assert json.loads(resource.contents[0].text)["catalog_sha256"] == manifest["catalog_sha256"]
            entry = await session.read_resource("etalon://skills/campaign")
            assert entry.contents[0].text == skills.read_member("campaign")["content"]
            reference = await session.read_resource("etalon://skills/campaign/references/legacy-workflow.md")
            assert reference.contents[0].text == skills.read_member("campaign", "references/legacy-workflow.md")["content"]
            role = await session.read_resource("etalon://host")
            assert skills.sha256(role.contents[0].text.encode()) == manifest["usage_method"]["sha256"]
            response = await session.call_tool("etalon_skill_read", {"slug": "campaign", "member": "../HOST.md"})
            assert not json.loads(response.content[0].text)["ok"]

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
    assert not list(target.rglob("*.sqlite"))
