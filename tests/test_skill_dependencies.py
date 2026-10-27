"""Selected exports carry required references without loading optional libraries."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from etalon import host, skills


def make_bundle(tmp_path, slug, *, required=(), optional=(), extra=None):
    root = tmp_path / slug
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: Dependency fixture\n---\nSelected fixture.\n",
        encoding="utf-8")
    (root / skills.DEPENDENCIES).write_text(json.dumps({
        "schema": "etalon-skill-dependencies/1", "required": list(required),
        "optional": list(optional)}), encoding="utf-8")
    for name, body in (extra or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return {"slug": slug, "bundle_ref": {"root": str(root)}}


@pytest.mark.parametrize("consumer", ["codex", "claude"])
def test_context_export_closes_real_links_without_reading_optional_skills(tmp_path, monkeypatch, consumer):
    original = skills.bundle_root
    calls = []
    def selected_only(slug):
        assert slug == "research-context"
        calls.append(slug)
        return original(slug)
    monkeypatch.setattr(skills, "bundle_root", selected_only)
    # snapshot still means one exact skill; callers use the exporter for a host.
    manifest, _ = skills.snapshot("research-context")
    assert calls == ["research-context"]
    assert manifest["dependencies"]["required"] == []
    assert "research-decision" in manifest["dependencies"]["optional"]
    calls.clear()
    target = tmp_path / consumer
    report = host.export(target, consumer, selected_skills=["research-context"])
    assert calls == ["research-context"]
    assert report["selected_skills"] == ["research-context"]
    assert [row["slug"] for row in report["skills"]] == calls
    directory = target / (".agents/skills" if consumer == "codex" else ".claude/skills")
    for path in directory.rglob("*.md"):
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" not in link and not link.startswith("#"):
                assert (path.parent / link.split("#", 1)[0]).is_file(), (path, link)
    assert host.verify(target)["matches_current_source"]
    assert not (directory / "etalon-skill-improvement").exists()
    assert not (directory / "etalon-research-decision").exists()


def test_empty_selection_never_reads_a_skill_and_none_preserves_all(tmp_path, monkeypatch):
    original = skills.bundle_root
    monkeypatch.setattr(skills, "bundle_root", lambda _: pytest.fail("empty selection read a skill"))
    report = host.export(tmp_path / "empty", selected_skills=[])
    assert report["skills"] == [] and report["selected_skills"] == []
    assert host.verify(tmp_path / "empty")["matches_current_source"]
    monkeypatch.setattr(skills, "bundle_root", original)
    legacy = host.export(tmp_path / "legacy")
    assert [row["slug"] for row in legacy["skills"]] == list(skills.WORKFLOWS)
    assert "selected_skills" not in legacy
    assert host.verify(tmp_path / "legacy")["matches_current_source"]


def test_optional_decision_member_can_be_read_without_expanding_installation(tmp_path):
    target = tmp_path / "context-only"
    host.export(target, selected_skills=["research-context"])
    manifest_before = (target / host.MANIFEST).read_bytes()
    member = skills.read_member("research-decision", "references/action-learning.md")
    manifest, contents = skills.snapshot("research-decision")
    assert member["content"] == contents["references/action-learning.md"].decode("utf-8")
    assert member["manifest_sha256"] == manifest["manifest_sha256"]
    assert (target / host.MANIFEST).read_bytes() == manifest_before
    assert not (target / ".agents/skills/etalon-research-decision").exists()


def test_required_builtin_dependency_still_exports_its_member(tmp_path, monkeypatch):
    first = make_bundle(tmp_path, "first", required=["second"], extra={
        "references/use.md": "[needed member](../../second/references/needed.md)\n"})
    second = make_bundle(tmp_path, "second", extra={"references/needed.md": "Required capability."})
    roots = {row["slug"]: Path(row["bundle_ref"]["root"]) for row in (first, second)}
    monkeypatch.setattr(skills, "WORKFLOWS", {slug: slug for slug in roots})
    monkeypatch.setattr(skills, "bundle_root", lambda slug: roots[slug])
    selection, snapshots = skills.snapshot_selection([{"slug": "first"}])
    assert selection == [{"slug": "first"}, {"slug": "second"}]
    assert snapshots[1][1]["references/needed.md"] == b"Required capability."


def test_portable_rebind_preserves_selected_closure_and_never_widens(tmp_path):
    entry = tmp_path / "entry.json"
    entry.write_text("{}", encoding="utf-8")
    original = tmp_path / "original"
    host.install(original, research_entry=entry, selected_skills=["research-context"])
    moved = tmp_path / "moved"
    shutil.copytree(original, moved)
    assert host.install(moved, research_entry=entry)["status"] == "rebound"
    saved = json.loads((moved / host.MANIFEST).read_text(encoding="utf-8"))
    assert saved["selected_skills"] == ["research-context"]
    assert {row["slug"] for row in saved["skills"]} == {"research-context"}
    assert host.install(moved, research_entry=entry)["status"] == "already_installed"
    before = (moved / host.MANIFEST).read_bytes()
    with pytest.raises(ValueError, match="cannot remove selected skills"):
        host.install(moved, research_entry=entry, selected_skills=["skill-improvement"])
    assert (moved / host.MANIFEST).read_bytes() == before


def test_dependency_metadata_is_versioned_and_saved_with_the_bundle(tmp_path):
    chosen = make_bundle(tmp_path, "custom", optional=["skill-improvement"])
    original, _ = skills.snapshot("custom", bundle_ref=chosen["bundle_ref"])
    saved = skills.persist_snapshot("custom", tmp_path / "saved", bundle_ref=chosen["bundle_ref"])
    path = Path(chosen["bundle_ref"]["root"]) / skills.DEPENDENCIES
    value = json.loads(path.read_text(encoding="utf-8"))
    value["optional"] = []
    path.write_text(json.dumps(value), encoding="utf-8")
    revised, _ = skills.snapshot("custom", bundle_ref=chosen["bundle_ref"])
    assert revised["manifest_sha256"] != original["manifest_sha256"]
    assert skills.verify_snapshot(saved) == original


def test_archived_dependencies_need_explicit_sources_and_never_fall_back(tmp_path, monkeypatch):
    first = make_bundle(tmp_path, "first", required=["second"])
    second = make_bundle(tmp_path, "second", required=["first"])
    monkeypatch.setattr(skills, "bundle_root", lambda _: pytest.fail("archive used current library"))
    with pytest.raises(ValueError, match="explicitly selected"):
        skills.snapshot_selection([first])
    # Cycles preserve exact explicit versions and terminate without re-reading.
    selection, snapshots = skills.snapshot_selection([first, second])
    assert selection == [first, second]
    assert [manifest["slug"] for manifest, _ in snapshots] == ["first", "second"]


def test_partial_dependency_exclusions_are_not_silently_restored(tmp_path):
    first = make_bundle(tmp_path, "first", required=["second"], extra={
        "references/use.md": "[needed member](../../second/references/needed.md)\n"})
    second = make_bundle(tmp_path, "second", extra={"references/needed.md": "Exact dependency."})
    second["members"] = ["SKILL.md"]
    with pytest.raises(ValueError, match="required skill member is unavailable"):
        skills.snapshot_selection([first, second])
    # Explicitly omitting the referencing file remains an exact partial choice.
    first["members"] = ["SKILL.md"]
    _, snapshots = skills.snapshot_selection([first, second])
    assert all(set(contents) == {"SKILL.md"} for _, contents in snapshots)


@pytest.mark.parametrize("selected", [["unknown"], ["research-context", "research-context"]])
def test_invalid_selection_fails_before_creating_a_project(tmp_path, selected):
    with pytest.raises(ValueError):
        host.export(tmp_path / "invalid", selected_skills=selected)
    assert not (tmp_path / "invalid").exists()


def test_cli_selected_export_and_verify(tmp_path):
    target = tmp_path / "cli"
    result = subprocess.run([sys.executable, "-m", "etalon.host", "export", "--target", str(target),
                             "--skills", "research-context"], capture_output=True, text=True,
                            encoding="utf-8", check=True, timeout=30)
    report = json.loads(result.stdout)
    assert [row["slug"] for row in report["skills"]] == ["research-context"]
    assert host.verify(target)["matches_current_source"]
