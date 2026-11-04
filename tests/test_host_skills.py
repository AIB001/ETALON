"""Native discovery installs preserve project settings and local edits."""
import json

import pytest

from etalon import host, host_skills, skills


def test_selected_real_bundles_preserve_settings_and_repeat_without_writes(tmp_path):
    root = tmp_path / "project"
    instructions = root / "AGENTS.md"
    settings = root / ".codex/config.toml"
    other = root / ".agents/skills/unrelated/SKILL.md"
    for path, content in ((instructions, b"existing instructions"), (settings, b"existing config"),
                          (other, b"existing skill")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    original = {path: path.read_bytes() for path in (instructions, settings, other)}
    result = host.install_skills(root, selected_skills=["research", "method-optimization", "skill-improvement"])
    assert result["verified"] and "etalon-research-decision" in result["skills"]
    for name in result["skills"]:
        _, members = skills.snapshot(name)
        for member, data in members.items():
            assert (root / ".agents/skills" / name / member).read_bytes() == data
    states = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in root.rglob("*")
              if path.is_file() and path.suffix != ".lock"}
    again = host.install_skills(root, selected_skills=["research", "method-optimization", "skill-improvement"])
    assert again["written_files"] == again["removed_files"] == 0
    assert all(path.read_bytes() == content for path, content in original.items())
    assert all((path.stat().st_mtime_ns, path.read_bytes()) == state for path, state in states.items())


@pytest.fixture
def local_bundle(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: etalon-research\ndescription: fixture\n---\nOriginal guidance\n")
    (source / "old.txt").write_text("original helper")
    monkeypatch.setattr(skills, "bundle_root", lambda slug: source)
    return source, tmp_path / "target"


def test_update_removes_only_owned_obsolete_members_and_preserves_unselected(local_bundle):
    source, root = local_bundle
    host.install_skills(root, selected_skills=["research"])
    unrelated = root / ".agents/skills/other/SKILL.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("unrelated")
    (source / "old.txt").unlink()
    (source / "new.txt").write_text("new helper")
    result = host.install_skills(root, selected_skills=["research"])
    assert result["removed_files"] == result["written_files"] == 1
    assert not (root / ".agents/skills/etalon-research/old.txt").exists()
    assert unrelated.read_text() == "unrelated"


def test_local_edit_conflict_is_detected_before_any_other_file_changes(local_bundle):
    source, root = local_bundle
    host.install_skills(root, selected_skills=["research"])
    edited = root / ".agents/skills/etalon-research/SKILL.md"
    edited.write_text("user edit")
    record = (root / host_skills.MANIFEST).read_bytes()
    (source / "new.txt").write_text("new helper")
    with pytest.raises(ValueError, match="local or unowned"):
        host.install_skills(root, selected_skills=["research"])
    assert edited.read_text() == "user edit"
    assert (root / host_skills.MANIFEST).read_bytes() == record
    assert not (edited.parent / "new.txt").exists()


def test_interrupted_initial_copy_can_be_retried(local_bundle, monkeypatch):
    source, root = local_bundle
    original = host_skills._write
    calls = []

    def interrupted(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("injected interruption")
        original(path, data)

    monkeypatch.setattr(host_skills, "_write", interrupted)
    with pytest.raises(OSError, match="injected interruption"):
        host.install_skills(root, selected_skills=["research"])
    monkeypatch.setattr(host_skills, "_write", original)
    result = host.install_skills(root, selected_skills=["research"])
    assert result["verified"]
    assert (root / ".agents/skills/etalon-research/old.txt").read_bytes() == (source / "old.txt").read_bytes()


def test_owned_inventory_cannot_remove_project_instructions(local_bundle):
    source, root = local_bundle
    host.install_skills(root, selected_skills=["research"])
    record_path = root / host_skills.MANIFEST
    record = json.loads(record_path.read_bytes())
    instructions = root / "AGENTS.md"
    instructions.write_text("keep")
    record["bundles"]["etalon-research"]["files"]["AGENTS.md"] = skills.sha256(b"keep")
    record_path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="outside its bundle"):
        host.install_skills(root, selected_skills=["research"])
    assert instructions.read_text() == "keep"
