"""Explicit workspace relocation of exact skill versions; never adoption.

Only a caller-supplied bounded map is consulted. Roots remain confined to the
workspace manifest directory; no directory discovery or current-version fallback
may repair a broken mapping. Original resource/workset bytes stay unchanged.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from etalon import skills
from .evidence_refs import EvidenceError, EvidenceWorkspace, _sha

MAX_SKILL_BUNDLES = 64


def _workspace(workspace, base=None):
    if isinstance(workspace, dict):
        if base is None or not Path(base).is_absolute():
            raise EvidenceError("skill bundle map manifest requires an absolute base directory")
        return workspace, Path(base)
    if base is not None:
        raise EvidenceError("base applies only to an explicit manifest object")
    if isinstance(workspace, EvidenceWorkspace):
        return workspace.manifest, workspace.base
    from .evidence_refs import load_workspace
    value = load_workspace(workspace)
    return value.manifest, value.base


def _slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_-]+", value):
        raise EvidenceError("skill bundle map requires an explicit lowercase slug")
    return value


def _root(base, relative):
    if (not isinstance(relative, str) or not relative or ":" in relative or "\\" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise EvidenceError("skill bundle root must be a canonical relative path inside the workspace")
    root = base.joinpath(*relative.split("/"))
    if any(skills.redirected(p) for p in (root, *root.parents)):
        raise EvidenceError("skill bundle root cannot use symlinks or junctions")
    try:
        root.resolve().relative_to(base.resolve())
    except ValueError:
        raise EvidenceError("skill bundle root escapes the workspace") from None
    return root


def validate_skill_bundle_map(workspace, *, base=None):
    """Validate a workspace/path, or a manifest dict with explicit absolute base.

    Return copies of the entries, retaining their canonical relative roots.
    Every declared bundle is byte-verified, including entries not selected now.
    This function can be called by load_workspace with (manifest, base=base)
    without recursively loading the workspace.
    """
    manifest, directory = _workspace(workspace, base)
    entries = manifest.get("skill_bundle_map", [])
    if not isinstance(entries, list) or len(entries) > MAX_SKILL_BUNDLES:
        raise EvidenceError("skill_bundle_map must contain at most 64 explicit bundles")
    result, seen = [], set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"slug", "manifest_sha256", "root"}:
            raise EvidenceError("skill bundle mapping requires slug, manifest_sha256 and root")
        _slug(item["slug"])
        _sha(item["manifest_sha256"])
        key = item["slug"], item["manifest_sha256"]
        if key in seen:
            raise EvidenceError("duplicate or conflicting skill bundle mapping")
        seen.add(key)
        root = _root(directory, item["root"])
        try:
            version, _ = skills.snapshot(item["slug"], bundle_ref={
                "root": str(root), "manifest_sha256": item["manifest_sha256"]})
        except (OSError, ValueError, KeyError) as exc:
            raise EvidenceError("mapped skill bundle unavailable or changed: " + str(exc)) from exc
        from .read_scope import CURRENT
        scope = CURRENT.get()
        if scope is not None:
            for member in version["files"]:
                scope.observe_file(root / member["path"], {k: member[k] for k in ("sha256", "bytes")})
        result.append(dict(item))
    return result


def resolve_skill_bundle(resource_content_ref, workspace=None):
    """Return a verified mapped bundle_ref, or the original bundle_ref/None.

    Maps may contain multiple versions of a slug. A declared slug with no entry
    for the requested hash is an explicit version gap, not a current-library hint.
    A missing/unavailable/mismatched mapped bundle never falls back to current.
    """
    if not isinstance(resource_content_ref, dict):
        raise EvidenceError("skill content reference required")
    slug = _slug(resource_content_ref.get("slug"))
    expected = resource_content_ref.get("manifest_sha256")
    _sha(expected)
    original = resource_content_ref.get("bundle_ref")
    if original is not None:
        if not isinstance(original, dict) or set(original) - {"root", "manifest_sha256"}:
            raise EvidenceError("skill bundle_ref needs root and optional manifest_sha256")
        if original.get("manifest_sha256", expected) != expected:
            raise EvidenceError("explicit skill bundle_ref conflicts with the resource version")
    if workspace is None:
        return dict(original) if original is not None else None
    manifest, directory = _workspace(workspace)
    entries = validate_skill_bundle_map(manifest, base=directory)
    matching = [item for item in entries if item["slug"] == slug and item["manifest_sha256"] == expected]
    if matching:
        return {"root": str(_root(directory, matching[0]["root"])), "manifest_sha256": expected}
    if any(item["slug"] == slug for item in entries):
        raise EvidenceError("workspace has no mapped bundle for the requested skill version")
    return dict(original) if original is not None else None
