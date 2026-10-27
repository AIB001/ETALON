"""Export project-local host adapters from the actual running installation.

No global host configuration is edited. Exports require a new directory; their
manifest detects drift without silently replacing an older capability version.
"""

from __future__ import annotations

from etalon.runtime.files import _consumer_lock

import argparse
import json
import os
import base64
import shlex
import sys
from pathlib import Path, PurePosixPath

from etalon import skills

MANIFEST = "etalon-host-manifest.json"


def _json(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _source() -> dict:
    return {"python": str(Path(sys.executable).absolute()),
            "pythonpath": str(Path(__file__).resolve().parent.parent)}


def _research_hook_files(host: str, source: dict, config: dict,
                         transport: str = "command") -> dict[str, bytes]:
    """Emit opt-in project hooks; never edit the active host's configuration."""
    from etalon.research_hooks import TOOL_PATTERN
    if set(config) != {"entry", "state_dir", "launcher"}:
        raise ValueError("unexpected research hook export configuration")
    for value in config.values():
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("research hook export paths must be absolute")
    if transport not in {"command", "mcp_tool"}:
        raise ValueError("hook transport must be command or mcp_tool")
    if transport == "mcp_tool":
        hooks = {}
        events = ["SessionStart", "PreToolUse", "PostToolUse", "PreCompact", "Stop"]
        if host == "claude":
            events.append("PostToolUseFailure")
        for event in events:
            payload = {"hook_event_name": event, "session_id": "${session_id}"}
            if event == "SessionStart":
                payload["source"] = "${source}"
            if event in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
                payload.update(tool_name="${tool_name}", tool_use_id="${tool_use_id}",
                               tool_input="${tool_input}")
            if event == "PostToolUse":
                payload["tool_response"] = "${tool_response}"
            handler = {"type": "mcp_tool", "server": "etalon",
                       "tool": "etalon_research_hook", "input": {"event": payload},
                       "timeout": 30}
            if host == "codex" and event in {"SessionStart", "PreToolUse"}:
                handler["additionalContextLimit"] = 6000
            group = {"hooks": [handler]}
            if event in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
                group["matcher"] = TOOL_PATTERN
            hooks[event] = [group]
        config_name = ".codex/hooks.json" if host == "codex" else ".claude/settings.json"
        return {config_name: _json({"hooks": hooks}),
                ".etalon/research-hook-binding.json": _json({
                    "schema": "etalon-research-hook-binding/1", "host": host,
                    "entry": config["entry"], "state_dir": config["state_dir"],
                    "max_bytes": 16384})}
    launcher = ("# Generated from the active ETALON installation.\n"
                "import sys\n"
                f"sys.path.insert(0, {source['pythonpath']!r})\n"
                "from etalon.research_hooks import main\n"
                f"raise SystemExit(main({['--host', host, '--entry', config['entry'], '--state-dir', config['state_dir']]!r}))\n")
    if host == "claude":
        # Current Claude command hooks support direct exec form, without a shell.
        command = {"type": "command", "command": source["python"], "args": [config["launcher"]]}
    else:
        posix = shlex.join([source["python"], config["launcher"]])
        def ps_quote(value):
            return "'" + value.replace("'", "''") + "'"
        powershell = "& " + " ".join(ps_quote(v) for v in (source["python"], config["launcher"])) + "; exit $LASTEXITCODE"
        # Encode only the fixed invocation, not data from tool event stdin.
        encoded = base64.b64encode(powershell.encode("utf-16-le")).decode("ascii")
        command = {"type": "command", "command": posix,
                   "commandWindows": f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded}"}
    hooks = {}
    for event in ("SessionStart", "PreToolUse", "PostToolUse", "PreCompact", "Stop"):
        handler = dict(command)
        # This bridge only reads local evidence. Do not inherit a host's
        # multi-minute default timeout when a source or filesystem is stuck.
        handler["timeout"] = 30
        if host == "codex" and event in {"SessionStart", "PreToolUse"}:
            handler["additionalContextLimit"] = 6000
        group = {"hooks": [handler]}
        if event in {"PreToolUse", "PostToolUse"}:
            group["matcher"] = TOOL_PATTERN
        hooks[event] = [group]
    if host == "claude":
        hooks["PostToolUseFailure"] = [{"matcher": TOOL_PATTERN, "hooks": [{**command, "timeout": 30}]}]
    config_name = ".codex/hooks.json" if host == "codex" else ".claude/settings.json"
    return {".etalon/research-hook.py": launcher.encode("utf-8"), config_name: _json({"hooks": hooks})}


def _payload(host: str, research_hooks: dict | None = None, task_workset: dict | None = None,
             *, hook_transport: str = "command", portable: bool = False,
             selected_skills: list[str] | None = None) -> tuple[dict, dict[str, bytes]]:
    if host not in {"codex", "claude"}:
        raise ValueError("host must be codex or claude")
    source = _source()
    files: dict[str, bytes] = {}
    versions = []
    directory = ".agents/skills" if host == "codex" else ".claude/skills"
    if task_workset is not None and selected_skills is not None:
        raise ValueError("choose task_workset or selected_skills, not both")
    if task_workset is None:
        selection = [{"slug": slug} for slug in
                     skills._selection(selected_skills, skills.WORKFLOWS, "selected_skills")]
    else:
        from etalon.research.meta_context import selected_skill_members
        task_workspace = None
        if research_hooks is not None:
            from etalon.research.entry import resolve_entry
            task_workspace = resolve_entry(research_hooks["entry"])[0]
        selection = selected_skill_members(task_workset, workspace=task_workspace)
        files[".etalon/task-workset.json"] = _json(task_workset)
    closure, snapshots = skills.snapshot_selection(selection)
    for manifest, contents in snapshots:
        versions.append(manifest)
        for name, data in contents.items():
            files[f"{directory}/{manifest['name']}/{name}"] = data
    role = skills.ROLE_PATH.read_bytes()
    files["AGENTS.md" if host == "codex" else "CLAUDE.md"] = role
    setup = skills.ROLE_PATH.with_name("HOST_SETUP.md")
    if setup.is_file():
        files["HOST_SETUP.md"] = setup.read_bytes()
    guides = skills.ROLE_PATH.with_name("host-guides")
    if guides.is_dir():
        if skills.redirected(guides):
            raise ValueError("host guides cannot be redirected")
        for path in sorted(guides.glob("*.md")):
            if skills.redirected(path) or not path.is_file() or path.stat().st_size > skills.MAX_FILE_BYTES:
                raise ValueError("host guide must be a bounded regular file")
            files[f"host-guides/{path.name}"] = path.read_bytes()
    launch = {"command": source["python"], "args": ["-m", "etalon.mcp"],
              "env": {"PYTHONPATH": source["pythonpath"], "PYTHONUTF8": "1"}}
    if research_hooks and hook_transport == "mcp_tool":
        launch["env"]["ETALON_RESEARCH_HOOK_BINDING"] = str(
            Path(research_hooks["launcher"]).parent / "research-hook-binding.json")
    if host == "codex":
        files[".codex/config.toml"] = (
            "[mcp_servers.etalon]\n"
            f"command = {json.dumps(launch['command'])}\n"
            'args = ["-m", "etalon.mcp"]\n'
            "\n[mcp_servers.etalon.env]\n"
            f"PYTHONPATH = {json.dumps(source['pythonpath'])}\n"
            'PYTHONUTF8 = "1"\n' +
            (f"ETALON_RESEARCH_HOOK_BINDING = {json.dumps(launch['env']['ETALON_RESEARCH_HOOK_BINDING'])}\n"
             if "ETALON_RESEARCH_HOOK_BINDING" in launch["env"] else "")
        ).encode()
    else:
        files[".mcp.json"] = _json({"mcpServers": {"etalon": {"type": "stdio", **launch}}})
    if research_hooks is not None:
        files.update(_research_hook_files(host, source, research_hooks, hook_transport))
    if portable:
        files["etalon-install.json"] = _json({"schema": "etalon-host-install/1",
            "host": host, "hook_transport": hook_transport,
            "entry": "supply_explicit_local_or_server_entry_on_install",
            "runtime": "python_running_etalon_host_install",
            "trust": "host_native_review_required_for_new_definitions"})
    manifest = {
        "schema": "etalon-host-export/1", "host": host, "source": source,
        "skills": versions,
        "catalog_sha256": skills.digest({v["slug"]: v["manifest_sha256"] for v in versions}),
        "usage_method": {"kind": "usage_method", "sha256": skills.sha256(role)},
        "files": {name: {"bytes": len(data), "sha256": skills.sha256(data)}
                  for name, data in sorted(files.items())},
        "host_validation": "not performed; export is not evidence of native host discovery",
    }
    if research_hooks is not None:
        manifest["research_hooks"] = research_hooks
    if hook_transport != "command" or portable:
        manifest["hook_transport"] = hook_transport
        manifest["portable"] = portable
    if task_workset is not None:
        manifest["task_workset_digest"] = task_workset["digest"]
        manifest["skill_selection"] = selection
    if selected_skills is not None:
        manifest["selected_skills"] = list(selected_skills)
        manifest["skill_selection"] = selection
    if closure != selection:
        manifest["skill_dependency_closure"] = closure
    return manifest, files


def _root(target: str | Path) -> Path:
    root = Path(target).absolute()
    if any(skills.redirected(path) for path in (root, *root.parents)):
        raise ValueError("host export path cannot contain symlinks or junctions")
    return root


def export(target: str | Path, host: str = "codex", *, research_entry: str | Path | None = None,
           task_workset: dict | None = None, hook_transport: str = "command",
           portable: bool = False, selected_skills: list[str] | None = None) -> dict:
    """Create an export without modifying existing projects or host settings."""
    root = _root(target)
    config = None
    if research_entry is not None:
        entry = _root(research_entry)
        if not entry.is_file():
            raise ValueError("research entry must be an existing local JSON file")
        config = {"entry": str(entry), "state_dir": str(root / ".etalon" / "research-hook-receipts"),
                  "launcher": str(root / ".etalon" / "research-hook.py")}
    if hook_transport not in {"command", "mcp_tool"}:
        raise ValueError("hook transport must be command or mcp_tool")
    manifest, files = _payload(host, config, task_workset,
                               hook_transport=hook_transport, portable=portable,
                               selected_skills=selected_skills)
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
    workset = None
    if "task_workset_digest" in saved:
        workset_path = _member(root, ".etalon/task-workset.json")
        if workset_path.stat().st_size > 32768:
            raise ValueError("exported workset exceeds limit")
        workset = json.loads(workset_path.read_text(encoding="utf-8"))
        if workset.get("digest") != saved["task_workset_digest"]:
            raise ValueError("export workset identity mismatch")
    current, current_files = _payload(saved["host"], saved.get("research_hooks"), workset,
        hook_transport=saved.get("hook_transport", "command"), portable=saved.get("portable", False),
        selected_skills=saved.get("selected_skills"))
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
    bundle_root = _member(root, directory)
    if bundle_root.exists():
        for path in bundle_root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            _member(root, relative)
            if path.is_file() and relative not in files:
                unexpected.append(relative)
    guide_root = _member(root, "host-guides")
    if guide_root.exists():
        for path in guide_root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            _member(root, relative)
            if path.is_file() and relative not in files:
                unexpected.append(relative)
    # Compare the inventory as well as self-reported digests. Edited manifests
    # cannot hide a missing reference or pass as the current source version.
    source_matches = all(saved.get(key) == current.get(key) for key in
                         ("skills", "catalog_sha256", "usage_method", "files",
                          "selected_skills", "skill_selection", "skill_dependency_closure"))
    runtime_matches = saved.get("source") == current["source"]
    return {"target": str(root), "export_matches_manifest": not (changed or missing or unexpected),
            "matches_current_source": source_matches, "runtime_matches": runtime_matches,
            "changed": changed, "missing": missing, "unexpected": sorted(unexpected),
            "catalog_sha256": saved.get("catalog_sha256"),
            "current_catalog_sha256": current["catalog_sha256"],
            "host_validation": saved.get("host_validation"),
            "current_file_count": len(current_files)}



def record_method_use(journal, **fields) -> str:
    """Append actual selection/partial use/stopping via the existing research journal.

    Availability or server reads alone are not tool-observed method use. Callers
    supply the evidence basis; journal validation enforces the record contract.
    """
    from etalon.research import MethodUse
    return journal.record(MethodUse(**fields))


def hook_request_id(event_ref: dict, handler_method_version: str) -> str:
    """Stable across replay, including submit succeeded but cursor save failed."""
    if (not isinstance(event_ref, dict) or not event_ref.get("log_id")
            or type(event_ref.get("sequence")) is not int or event_ref["sequence"] < 1
            or not handler_method_version):
        raise ValueError("hook needs a committed event reference and handler version")
    return "hook-" + skills.digest({"event_ref": event_ref, "handler_method_version": handler_method_version})




def _save_cursor(path: Path, value: dict) -> None:
    temporary = _root(str(path) + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(_json(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def consume_events(log_ref: dict, *, state_path: str | Path, handler_method_version: str,
                   handler, event_types, record_types=(), limit: int = 100) -> dict:
    """Consume one bounded page of committed events, with an explicit optional hook.

    The callback receives (event, stable_request_id). If it submits work, it must
    use that request ID and the same action semantics on replay. Callback effects
    other than idempotent ETALON submissions are the callback's responsibility.
    Exceptions preserve the cursor before the failed event and do not roll back
    scientific facts. No polling, retries or budget expansion are performed.
    """
    from etalon.runtime.api import read_events
    if not isinstance(handler_method_version, str) or not handler_method_version.strip():
        raise ValueError("handler_method_version is required")
    if isinstance(event_types, str) or isinstance(record_types, str):
        raise ValueError("event and record types must be collections")
    event_types, record_types = set(event_types), set(record_types)
    if "research_recorded" in event_types and not record_types:
        raise ValueError("research hooks require explicit record schema filters")
    path = _root(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _consumer_lock(path):
        saved = None
        if path.exists():
            if path.stat().st_size > skills.MAX_FILE_BYTES:
                raise ValueError("consumer cursor exceeds size limit")
            saved = json.loads(path.read_text(encoding="utf-8"))
            if (saved.get("schema") != "etalon-hook-cursor/1"
                    or saved.get("handler_method_version") != handler_method_version
                    or saved.get("log_ref") != log_ref
                    or saved.get("event_types") != sorted(event_types)
                    or saved.get("record_types") != sorted(record_types)):
                raise ValueError("cursor belongs to a different log, handler version or filter")
        page = read_events(log_ref, after_sequence=saved["cursor"] if saved else 0, limit=limit)
        state = {"schema": "etalon-hook-cursor/1", "log_ref": log_ref,
                 "handler_method_version": handler_method_version,
                 "event_types": sorted(event_types), "record_types": sorted(record_types),
                 "cursor": {"log_id": page["log_id"], "after_sequence": 0}}
        if saved:
            state["cursor"] = saved["cursor"]
        handled, skipped = [], []
        for event in page["events"]:
            ref = event["event_ref"]
            if (ref["log_id"] != page["log_id"] or ref["sequence"] <= state["cursor"]["after_sequence"]):
                raise ValueError("event log identity or order mismatch")
            selected = event["kind"] in event_types
            if event["kind"] == "research_recorded":
                selected = selected and event.get("body", {}).get("record", {}).get("schema") in record_types
            if selected:
                request_id = hook_request_id(ref, handler_method_version)
                handler(event, request_id)
                handled.append({"event_ref": ref, "request_id": request_id})
            else:
                skipped.append(ref)
            state["cursor"] = {"log_id": page["log_id"], "after_sequence": ref["sequence"]}
            _save_cursor(path, state)
        # Also persist log identity for an empty page; never advance past unread facts.
        if not page["events"]:
            state["cursor"] = page["next_cursor"]
            _save_cursor(path, state)
        return {"handled": handled, "skipped": skipped, "next_cursor": state["cursor"],
                "has_more": page["has_more"], "delivery": "at_least_once_with_stable_request_id"}

def install(target, *, research_entry, host="codex", hook_transport="mcp_tool", selected_skills=None):
    """Install or relocate an owned portable project, preserving previous bytes."""
    from etalon.host_install import install as bind
    return bind(target, research_entry=research_entry, host=host, hook_transport=hook_transport,
                selected_skills=selected_skills)


def doctor(target):
    """Inspect portable runtime bindings without claiming host trust or delivery."""
    from etalon.host_install import doctor as inspect
    return inspect(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("catalog", help="inspect installed capability and usage-method versions")
    index.add_argument("--detail", choices=("full", "summary"), default="full")
    index.add_argument("--skills", nargs="*", default=None,
                       help="exact skill slugs; omit for all, supply no slugs for none")
    create = sub.add_parser("export", help="export to a new project directory")
    create.add_argument("--target", type=Path, required=True)
    create.add_argument("--host", choices=("codex", "claude"), default="codex")
    create.add_argument("--research-entry", type=Path,
                        help="opt in to project-local research lifecycle hooks for this JSON entry")
    create.add_argument("--hook-transport", choices=("command", "mcp_tool"), default="command")
    create.add_argument("--portable", action="store_true",
                        help="include a recipe for explicit runtime/entry rebinding")
    create.add_argument("--skills", nargs="*", default=None,
                        help="selected skill slugs plus required dependencies; omit for all, empty for none")
    setup = sub.add_parser("install", help="create or rebind a portable host project")
    setup.add_argument("--target", type=Path, required=True)
    setup.add_argument("--host", choices=("codex", "claude"), default="codex")
    setup.add_argument("--research-entry", type=Path, required=True)
    setup.add_argument("--hook-transport", choices=("command", "mcp_tool"), default="mcp_tool")
    setup.add_argument("--skills", nargs="*", default=None,
                       help="selected skills plus required dependencies; rebind preserves the saved selection")
    diagnose = sub.add_parser("doctor", help="check bindings; native trust/delivery remain separate")
    diagnose.add_argument("--target", type=Path, required=True)
    check = sub.add_parser("verify", help="check an export for changed, missing or added files")
    check.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            report = {**skills.catalog(detail=args.detail, skills=args.skills), "source": _source()}
        elif args.command == "export":
            report = export(args.target, args.host, research_entry=args.research_entry,
                            hook_transport=args.hook_transport, portable=args.portable,
                            selected_skills=args.skills)
        elif args.command == "install":
            report = install(args.target, host=args.host, research_entry=args.research_entry,
                             hook_transport=args.hook_transport, selected_skills=args.skills)
        elif args.command == "doctor":
            report = doctor(args.target)
        else:
            report = verify(args.target)
        good = args.command != "verify" or all(report[key] for key in
                    ("export_matches_manifest", "matches_current_source", "runtime_matches"))
        if args.command == "doctor":
            good = report["configuration_verified"] and report["entry_exists_on_server"]
        print(_json({"ok": good, **report}).decode("utf-8"), end="")
        return 0 if good else 1
    except (ValueError, KeyError, OSError) as error:
        print(_json({"ok": False, "error": {"code": type(error).__name__,
                                             "message": str(error)}}).decode("utf-8"), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
