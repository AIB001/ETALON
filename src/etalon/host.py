"""Export project-local host adapters from the actual running installation.

No global host configuration is edited. Exports require a new directory; their
manifest detects drift without silently replacing an older capability version.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

from etalon import skills

MANIFEST = "etalon-host-manifest.json"


def _json(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _source() -> dict:
    return {"python": str(Path(sys.executable).absolute()),
            "pythonpath": str(Path(__file__).resolve().parent.parent)}


def _payload(host: str) -> tuple[dict, dict[str, bytes]]:
    if host not in {"codex", "claude"}:
        raise ValueError("host must be codex or claude")
    source = _source()
    files: dict[str, bytes] = {}
    versions = []
    directory = ".agents/skills" if host == "codex" else ".claude/skills"
    for slug in skills.WORKFLOWS:
        manifest, contents = skills.snapshot(slug)
        versions.append(manifest)
        for name, data in contents.items():
            files[f"{directory}/{manifest['name']}/{name}"] = data
    role = skills.ROLE_PATH.read_bytes()
    files["AGENTS.md" if host == "codex" else "CLAUDE.md"] = role
    launch = {"command": source["python"], "args": ["-m", "etalon.mcp"],
              "env": {"PYTHONPATH": source["pythonpath"], "PYTHONUTF8": "1"}}
    if host == "codex":
        files[".codex/config.toml"] = (
            "[mcp_servers.etalon]\n"
            f"command = {json.dumps(launch['command'])}\n"
            'args = ["-m", "etalon.mcp"]\n'
            "\n[mcp_servers.etalon.env]\n"
            f"PYTHONPATH = {json.dumps(source['pythonpath'])}\n"
            'PYTHONUTF8 = "1"\n'
        ).encode()
    else:
        files[".mcp.json"] = _json({"mcpServers": {"etalon": {"type": "stdio", **launch}}})
    manifest = {
        "schema": "etalon-host-export/1", "host": host, "source": source,
        "skills": versions,
        "catalog_sha256": skills.digest({v["slug"]: v["manifest_sha256"] for v in versions}),
        "usage_method": {"kind": "usage_method", "sha256": skills.sha256(role)},
        "files": {name: {"bytes": len(data), "sha256": skills.sha256(data)}
                  for name, data in sorted(files.items())},
        "host_validation": "not performed; export is not evidence of native host discovery",
    }
    return manifest, files


def _root(target: str | Path) -> Path:
    root = Path(target).absolute()
    if any(skills.redirected(path) for path in (root, *root.parents)):
        raise ValueError("host export path cannot contain symlinks or junctions")
    return root


def export(target: str | Path, host: str = "codex") -> dict:
    """Create an export without modifying existing projects or host settings."""
    root = _root(target)
    manifest, files = _payload(host)
    # Fail even for an existing empty directory. No existing role or config is replaced.
    root.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
    with (root / MANIFEST).open("xb") as handle:
        handle.write(_json(manifest))
    return {"target": str(root), "manifest": str(root / MANIFEST), **manifest}


def _member(root: Path, name: str) -> Path:
    if (not isinstance(name, str) or not name or ":" in name or "\\" in name
            or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("manifest member must be a canonical relative POSIX path")
    path = root
    for part in name.split("/"):
        path = path / part
        if skills.redirected(path):
            raise ValueError("exported members cannot be symlinks or junctions")
    return path


def verify(target: str | Path) -> dict:
    """Compare exported bytes and complete skill inventories with the current source."""
    root = _root(target)
    manifest_path = _member(root, MANIFEST)
    if manifest_path.stat().st_size > skills.MAX_BUNDLE_BYTES:
        raise ValueError("host manifest exceeds 4 MiB")
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    if saved.get("schema") != "etalon-host-export/1":
        raise ValueError("unsupported host export schema")
    current, current_files = _payload(saved["host"])
    files = saved.get("files")
    if not isinstance(files, dict) or not files or len(files) > 2000:
        raise ValueError("host manifest needs a bounded file inventory")
    changed, missing = [], []
    for name, expected in files.items():
        path = _member(root, name)
        if not path.exists():
            missing.append(name)
        elif (not path.is_file() or path.stat().st_size != expected["bytes"]
              or path.stat().st_size > skills.MAX_BUNDLE_BYTES
              or skills.sha256(path.read_bytes()) != expected["sha256"]):
            changed.append(name)
    directory = ".agents/skills" if saved["host"] == "codex" else ".claude/skills"
    unexpected = []
    for name in skills.WORKFLOWS.values():
        bundle = _member(root, f"{directory}/{name}")
        if bundle.exists():
            for path in bundle.rglob("*"):
                relative = path.relative_to(root).as_posix()
                _member(root, relative)
                if path.is_file() and relative not in files:
                    unexpected.append(relative)
    # Compare the inventory as well as self-reported digests. Edited manifests
    # cannot hide a missing reference or pass as the current source version.
    source_matches = all(saved.get(key) == current[key] for key in
                         ("skills", "catalog_sha256", "usage_method", "files"))
    runtime_matches = saved.get("source") == current["source"]
    return {"target": str(root), "export_matches_manifest": not (changed or missing or unexpected),
            "matches_current_source": source_matches, "runtime_matches": runtime_matches,
            "changed": changed, "missing": missing, "unexpected": sorted(unexpected),
            "catalog_sha256": saved.get("catalog_sha256"),
            "current_catalog_sha256": current["catalog_sha256"],
            "host_validation": saved.get("host_validation"),
            "current_file_count": len(current_files)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="inspect installed capability and usage-method versions")
    create = sub.add_parser("export", help="export to a new project directory")
    create.add_argument("--target", type=Path, required=True)
    create.add_argument("--host", choices=("codex", "claude"), default="codex")
    check = sub.add_parser("verify", help="check an export for changed, missing or added files")
    check.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            report = {**skills.catalog(), "source": _source()}
        elif args.command == "export":
            report = export(args.target, args.host)
        else:
            report = verify(args.target)
        good = args.command != "verify" or all(report[key] for key in
                    ("export_matches_manifest", "matches_current_source", "runtime_matches"))
        print(_json({"ok": good, **report}).decode("utf-8"), end="")
        return 0 if good else 1
    except (ValueError, KeyError, OSError) as error:
        print(_json({"ok": False, "error": {"code": type(error).__name__,
                                             "message": str(error)}}).decode("utf-8"), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
