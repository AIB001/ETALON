"""Read the same capability bundles from a checkout, a wheel, or a host export.

Digests describe observed bytes, not host invocation or scientific efficacy. A
bundle includes its references and scripts; changing or deleting either changes
its version. The usage guide is versioned separately from the domain skills.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

WORKFLOWS = {
    "campaign": "etalon-campaign",
    "sweep": "etalon-screening-sweep",
    "gate-calibration": "etalon-gate-calibration",
    "generation-planning": "etalon-generation-planning",
    "monitoring": "etalon-campaign-monitoring",
    "install": "etalon-install",
}
ROLE_PATH = Path(__file__).with_name("HOST.md")
MAX_FILE_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8"))


def redirected(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def bundle_root(slug: str) -> Path:
    if slug not in WORKFLOWS:
        raise KeyError(f"no such workflow {slug!r}; known: {sorted(WORKFLOWS)}")
    package = Path(__file__).resolve().parent
    repository = package.parents[1] / ".claude" / "skills" / WORKFLOWS[slug]
    installed = package / "mcp" / "skills" / "bundles" / slug
    for root in (repository, installed):
        if (root / "SKILL.md").is_file():
            return root
    raise FileNotFoundError(f"no capability bundle for {slug!r}: {repository} or {installed}")


def snapshot(slug: str) -> tuple[dict, dict[str, bytes]]:
    """Read a bounded bundle once, computing the manifest from exactly those bytes."""
    root = bundle_root(slug)
    if any(redirected(path) for path in (root, *root.parents)):
        raise ValueError("capability source cannot contain symlinks or junctions")
    contents: dict[str, bytes] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if redirected(path):
            raise ValueError("capability members cannot be symlinks or junctions")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("capability member must be a regular file of at most 256 KiB")
        data = path.read_bytes()
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_BUNDLE_BYTES:
            raise ValueError("capability bundle exceeds its read limit")
        contents[path.relative_to(root).as_posix()] = data
    entry = contents["SKILL.md"].decode("utf-8")
    front = entry.split("---", 2)
    if len(front) != 3 or front[0].strip():
        raise ValueError("skill entrypoint needs YAML frontmatter")
    # Our maintained frontmatter uses plain one-line names and descriptions.
    metadata = dict(line.split(": ", 1) for line in front[1].splitlines() if ": " in line)
    if metadata.get("name") != WORKFLOWS[slug] or not metadata.get("description", "").strip():
        raise ValueError("skill frontmatter must declare its name and description")
    manifest = {
        "schema": "etalon-capability-version/1", "kind": "domain_skill", "slug": slug,
        "name": metadata["name"], "description": metadata["description"],
        "entrypoint": "SKILL.md",
        "files": [{"path": name, "bytes": len(data), "sha256": sha256(data)}
                  for name, data in sorted(contents.items())],
    }
    return {**manifest, "manifest_sha256": digest(manifest)}, contents


def catalog() -> dict:
    skills = [snapshot(slug)[0] for slug in WORKFLOWS]
    role = ROLE_PATH.read_bytes()
    return {
        "schema": "etalon-capability-catalog/1", "skills": skills,
        "catalog_sha256": digest({row["slug"]: row["manifest_sha256"] for row in skills}),
        "usage_method": {"kind": "usage_method", "name": "etalon-host-guidance",
                         "sha256": sha256(role), "bytes": len(role), "uri": "etalon://host"},
        "load_evidence": "server-read-bytes; host discovery and invocation are not observed",
    }


def read_member(slug: str, member: str = "SKILL.md") -> dict:
    """Read only an exact manifest member, never an arbitrary path."""
    if (not isinstance(member, str) or not member or "\\" in member
            or PurePosixPath(member).is_absolute()
            or any(part in {"", ".", ".."} for part in member.split("/"))):
        raise ValueError("skill member must be a canonical relative POSIX path")
    manifest, contents = snapshot(slug)
    if member not in contents:
        raise KeyError(f"no member {member!r} in capability {slug!r}")
    data = contents[member]
    return {"slug": slug, "member": member, "content": data.decode("utf-8"),
            "sha256": sha256(data), "manifest_sha256": manifest["manifest_sha256"]}
