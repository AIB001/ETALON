"""Read the same capability bundles from a checkout, a wheel, or a host export.

Digests describe observed bytes, not host invocation or scientific efficacy. A
bundle includes its references and scripts; changing or deleting either changes
its version. The usage guide is versioned separately from the domain skills.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

WORKFLOWS = {
    "campaign": "etalon-campaign",
    "sweep": "etalon-screening-sweep",
    "gate-calibration": "etalon-gate-calibration",
    "generation-planning": "etalon-generation-planning",
    "monitoring": "etalon-campaign-monitoring",
    "install": "etalon-install",
    "data-design": "etalon-data-design",
    "research": "etalon-research",
    "research-context": "etalon-research-context",
    "research-decision": "etalon-research-decision",
    "research-index": "etalon-research-index",
    "method-configuration": "etalon-method-configuration",
    "skill-improvement": "etalon-skill-improvement",
    "molquarry-analogue-search": "molquarry-analogue-search",
    "molquarry-assay-literature": "molquarry-assay-literature",
    "molquarry-compound-sourcing": "molquarry-compound-sourcing",
    "molquarry-selectivity-evidence": "molquarry-selectivity-evidence",
    "molquarry-structure-templates": "molquarry-structure-templates",
    "molquarry-target-modulators": "molquarry-target-modulators",
}
ROLE_PATH = Path(__file__).with_name("HOST.md")
MAX_FILE_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 512
SNAPSHOT_MANIFEST = "etalon-bundle-manifest.json"


def library_catalog() -> dict:
    """Optional discovery groups; they do not constrain selection or duplicate bundles."""
    memory = ["research-context", "research", "research-index", "research-decision"]
    rsi = ["skill-improvement"]
    return {"schema": "etalon-skill-libraries/1", "libraries": {
        "memory": memory,
        "execution": [slug for slug in WORKFLOWS if slug not in memory + rsi],
        "rsi": rsi,
    }, "entrypoints": {slug: f"etalon://skills/{slug}" for slug in WORKFLOWS},
        "selection": "host chooses, combines, modifies, skips or stops; groups are discovery only"}


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
    if slug.startswith("molquarry-"):
        # Use the pinned asset's original bytes, including its supporting files.
        # The existing asset wheel mapping already carries the complete source.
        repository = package.parents[1] / "asset" / "molquarry" / "skills" / WORKFLOWS[slug]
        installed = package / "_assets" / "molquarry" / "skills" / WORKFLOWS[slug]
    else:
        repository = package.parents[1] / ".claude" / "skills" / WORKFLOWS[slug]
        installed = package / "mcp" / "skills" / "bundles" / slug
    for root in (repository, installed):
        if (root / "SKILL.md").is_file():
            return root
    raise FileNotFoundError(f"no capability bundle for {slug!r}: {repository} or {installed}")


def snapshot(slug: str, *, bundle_ref: dict | None = None) -> tuple[dict, dict[str, bytes]]:
    """Read a bounded bundle once, computing the manifest from exactly those bytes."""
    if bundle_ref is None:
        root = bundle_root(slug)
    else:
        if not isinstance(bundle_ref, dict) or set(bundle_ref) - {"root", "manifest_sha256"}:
            raise ValueError("bundle_ref needs root and optional manifest_sha256")
        root = Path(bundle_ref.get("root", ""))
        if not root.is_absolute() or not (root / "SKILL.md").is_file():
            raise ValueError("explicit bundle root must be absolute and contain SKILL.md")
    if not isinstance(slug, str) or not slug or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in slug):
        raise ValueError("bundle slug must be a lowercase identifier")
    if any(redirected(path) for path in (root, *root.parents)):
        raise ValueError("capability source cannot contain symlinks or junctions")
    contents: dict[str, bytes] = {}
    total = 0
    for index, path in enumerate(root.rglob("*")):
        if index >= MAX_MEMBERS:
            raise ValueError("capability bundle exceeds member limit")
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if redirected(path):
            raise ValueError("capability members cannot be symlinks or junctions")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("capability member must be a regular file of at most 256 KiB")
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_BUNDLE_BYTES:
            raise ValueError("capability bundle exceeds its read limit")
        name = path.relative_to(root).as_posix()
        _canonical_member(name)
        contents[name] = data
    entry = contents["SKILL.md"].decode("utf-8")
    front = entry.split("---", 2)
    if len(front) != 3 or front[0].strip():
        raise ValueError("skill entrypoint needs YAML frontmatter")
    # Our maintained frontmatter uses plain one-line names and descriptions.
    metadata = dict(line.split(": ", 1) for line in front[1].splitlines() if ": " in line)
    if (not metadata.get("name") or (bundle_ref is None and metadata["name"] != WORKFLOWS[slug])
            or not metadata.get("description", "").strip()):
        raise ValueError("skill frontmatter must declare its name and description")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", metadata["name"]):
        raise ValueError("skill name must be a safe single directory name")
    manifest = {
        "schema": "etalon-capability-version/1", "kind": "domain_skill", "slug": slug,
        "name": metadata["name"], "description": metadata["description"],
        "entrypoint": "SKILL.md",
        "files": [{"path": name, "bytes": len(data), "sha256": sha256(data)}
                  for name, data in sorted(contents.items())],
    }
    manifest = {**manifest, "manifest_sha256": digest(manifest)}
    if bundle_ref and bundle_ref.get("manifest_sha256") not in (None, manifest["manifest_sha256"]):
        raise ValueError("bundle manifest digest mismatch")
    return manifest, contents


def _selection(selected: list[str] | None, known, label: str) -> list[str]:
    """Select exact names; empty means none, never fall back to a full scan."""
    if selected is None:
        return list(known)
    if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
        raise ValueError(f"{label} must be a list of exact names or null")
    if len(set(selected)) != len(selected):
        raise ValueError(f"duplicate {label} names")
    unknown = [name for name in selected if name not in known]
    if unknown:
        raise ValueError(f"unknown {label}: {unknown}")
    return list(selected)


def catalog(*, detail: str = "full", skills: list[str] | None = None) -> dict:
    """Read selected bundles only. Defaults retain the complete legacy catalog."""
    if not isinstance(detail, str) or detail not in {"full", "summary"}:
        raise ValueError("detail must be full or summary")
    selected = _selection(skills, WORKFLOWS, "skills")
    manifests = [snapshot(slug)[0] for slug in selected]
    role = ROLE_PATH.read_bytes()
    rows = manifests if detail == "full" else [
        {key: value for key, value in manifest.items() if key != "files"}
        for manifest in manifests]
    report = {
        "schema": "etalon-capability-catalog/1", "skills": rows,
        "catalog_sha256": digest({row["slug"]: row["manifest_sha256"] for row in manifests}),
        "usage_method": {"kind": "usage_method", "name": "etalon-host-guidance",
                         "sha256": sha256(role), "bytes": len(role), "uri": "etalon://host"},
        "load_evidence": "server-read-bytes; host discovery and invocation are not observed",
    }
    if detail != "full" or skills is not None:
        report.update(detail=detail, scope={"skills": selected},
                      catalog_hash_scope="selected skill versions only; scope.skills defines coverage")
    return report


def read_member(slug: str, member: str = "SKILL.md", *, bundle_ref: dict | None = None) -> dict:
    """Read only an exact manifest member, never an arbitrary path."""
    _canonical_member(member)
    manifest, contents = snapshot(slug, bundle_ref=bundle_ref)
    if member not in contents:
        raise KeyError(f"no member {member!r} in capability {slug!r}")
    data = contents[member]
    return {"slug": slug, "member": member, "content": data.decode("utf-8"),
            "sha256": sha256(data), "manifest_sha256": manifest["manifest_sha256"]}


def _canonical_member(member: str) -> None:
    if (not isinstance(member, str) or not member or ":" in member or "\\" in member
            or PurePosixPath(member).is_absolute()
            or any(part in {"", ".", ".."} for part in member.split("/"))):
        raise ValueError("skill member must be a canonical relative POSIX path")


def persist_snapshot(slug: str, target: str | Path, *, bundle_ref: dict | None = None) -> dict:
    """Copy exactly the observed bytes to a new directory; never replace a prior version."""
    manifest, contents = snapshot(slug, bundle_ref=bundle_ref)
    root = Path(target).absolute()
    if any(redirected(path) for path in (root, *root.parents)):
        raise ValueError("snapshot target cannot contain symlinks or junctions")
    root.mkdir(parents=True, exist_ok=False)
    members = root / "bundle"
    members.mkdir()
    for name, data in contents.items():
        path = members / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
    with (root / SNAPSHOT_MANIFEST).open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return {"manifest_path": str(root / SNAPSHOT_MANIFEST),
            "manifest_sha256": manifest["manifest_sha256"]}


def verify_snapshot(content_ref: dict, *, resolver=None) -> dict:
    """Verify a saved bundle, not merely its claimed manifest hash."""
    if not isinstance(content_ref, dict) or set(content_ref) != {"manifest_path", "manifest_sha256"}:
        raise ValueError("snapshot content_ref needs manifest_path and manifest_sha256")
    path = (Path(content_ref["manifest_path"]) if resolver is None
            else resolver.resolve_snapshot(content_ref))
    if not path.is_absolute() or any(redirected(p) for p in (path, *path.parents)):
        raise ValueError("snapshot manifest must be absolute without redirected paths")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("snapshot manifest exceeds read limit")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    actual, _ = snapshot(manifest["slug"], bundle_ref={"root": str(path.parent / "bundle"),
                         "manifest_sha256": content_ref["manifest_sha256"]})
    if actual != manifest:
        raise ValueError("snapshot inventory or content mismatch")
    return actual
