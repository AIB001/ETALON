"""Discover the same skill resources in a checkout and an installed wheel."""

import re
from importlib.resources import files
from pathlib import Path, PurePosixPath

from .errors import MolQuarryError


def _root():
    packaged = files("molquarry").joinpath("skill_resources")
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "skills"


def _resource_names(directory, prefix=""):
    for child in sorted(directory.iterdir(), key=lambda item: item.name):
        name = f"{prefix}{child.name}"
        if child.is_dir():
            yield from _resource_names(child, f"{name}/")
        elif child.name.endswith((".md", ".yaml", ".json")):
            yield name


def list_skills() -> dict:
    """List packaged entrypoints and their available supporting files without network I/O."""
    rows = []
    for directory in sorted(_root().iterdir(), key=lambda item: item.name):
        entrypoint = directory.joinpath("SKILL.md")
        if not directory.is_dir() or not entrypoint.is_file():
            continue
        frontmatter = entrypoint.read_text(encoding="utf-8").split("---", 2)[1]
        metadata = dict(line.split(": ", 1) for line in frontmatter.splitlines() if ": " in line)
        rows.append(
            {
                "name": metadata["name"],
                "description": metadata["description"],
                "entrypoint": "SKILL.md",
                "resources": list(_resource_names(directory)),
            }
        )
    return {"ok": True, "skills": rows}


def read_skill(name: str, path: str = "SKILL.md") -> dict:
    """Read a listed skill resource, never an arbitrary local path."""
    relative = PurePosixPath(path)
    if (
        not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
        or not path
        or relative.is_absolute()
        or ".." in relative.parts
        or "\\" in path
    ):
        raise MolQuarryError("invalid_path", "Use a listed skill name and relative resource path")
    row = next((row for row in list_skills()["skills"] if row["name"] == name), None)
    if row is None:
        raise MolQuarryError("unknown_skill", f"Unknown skill: {name}")
    if path not in row["resources"]:
        raise MolQuarryError("unknown_skill_resource", f"Resource not listed for {name}: {path}")
    return {
        "ok": True,
        "name": name,
        "path": path,
        "content": _root().joinpath(name, *relative.parts).read_text(encoding="utf-8"),
    }
