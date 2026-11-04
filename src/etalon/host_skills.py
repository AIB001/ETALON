"""Sync selected Codex skills into an existing project without rebinding it.

Source bundles remain authoritative. The small ownership file permits updates
and interrupted-copy retries while preserving unrelated skills and local edits.
It does not alter AGENTS.md, MCP settings, research entries or running jobs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from etalon import skills
from etalon.runtime.files import _consumer_lock

MANIFEST = ".agents/etalon-skills.json"


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".etalon-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install(target, *, selected_skills):
    """Install explicit skills plus dependencies; a repeated sync writes nothing."""
    from etalon.host import _json, _member, _root

    chosen = skills.select_skills(selected_skills, "selected_skills")
    if selected_skills is None or not chosen:
        raise ValueError("select at least one skill explicitly")
    _, snapshots = skills.snapshot_selection([{"slug": slug} for slug in chosen])
    root = _root(target)
    record_path = _member(root, MANIFEST)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with _consumer_lock(record_path):
        saved = {"schema": "etalon-native-skills/1", "host": "codex", "bundles": {}}
        if record_path.exists():
            saved = json.loads(record_path.read_bytes())
            if (not isinstance(saved, dict)
                    or saved.get("schema") != "etalon-native-skills/1" or saved.get("host") != "codex"
                    or not isinstance(saved.get("bundles"), dict)):
                raise ValueError("invalid native skill ownership file")
        bundles = dict(saved["bundles"])
        writes, removals, conflicts, selected = {}, [], [], []
        for manifest, content in snapshots:
            name = manifest["name"]
            selected.append(name)
            prefix = ".agents/skills/" + name + "/"
            previous = bundles.get(name, {})
            if not isinstance(previous, dict):
                raise ValueError("invalid owned skill inventory: " + name)
            old = previous.get("files", {})
            if not isinstance(old, dict):
                raise ValueError("invalid owned skill inventory: " + name)
            desired = {prefix + member: data for member, data in content.items()}
            for member in old:
                if not member.startswith(prefix):
                    raise ValueError("owned skill member is outside its bundle")
                _member(root, member)
            directory = _member(root, prefix.rstrip("/"))
            if directory.exists():
                for path in directory.rglob("*"):
                    member = path.relative_to(root).as_posix()
                    _member(root, member)
                    if (path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
                            and member not in desired and member not in old):
                        conflicts.append(member)
            for member in desired.keys() | old.keys():
                path = _member(root, member)
                data = desired.get(member)
                current = path.read_bytes() if path.is_file() else None
                if path.exists() and not path.is_file():
                    conflicts.append(member)
                elif current is not None and current != data and skills.sha256(current) != old.get(member):
                    conflicts.append(member)
                elif data is not None and current != data:
                    writes[member] = data
                elif data is None and current is not None:
                    removals.append(member)
            bundles[name] = {"slug": manifest["slug"], "manifest_sha256": manifest["manifest_sha256"],
                             "files": {member: skills.sha256(data) for member, data in desired.items()}}
        # Preflight all selected bundles before replacing any installed content.
        if conflicts:
            raise ValueError("native skills contain local or unowned changes: " + ", ".join(sorted(conflicts)))
        for member, data in sorted(writes.items()):
            _write(_member(root, member), data)
        for member in sorted(removals):
            _member(root, member).unlink()
        record = _json({"schema": "etalon-native-skills/1", "host": "codex", "bundles": bundles})
        if not record_path.exists() or record_path.read_bytes() != record:
            _write(record_path, record)
        verified = all(_member(root, member).read_bytes() == data
                       for manifest, content in snapshots
                       for member, data in ((".agents/skills/" + manifest["name"] + "/" + key, value)
                                            for key, value in content.items()))
        if not verified:
            raise ValueError("native skill bytes changed during installation")
        return {"target": str(root), "manifest": str(record_path), "skills": selected,
                "written_files": len(writes), "removed_files": len(removals), "verified": True,
                "status": "updated" if writes or removals else "unchanged",
                "host_discovery": "files_installed; native discovery not observed by installer"}
