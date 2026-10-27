"""Small JSON commands over the existing research-note APIs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def register(sub):
    research = sub.add_parser("research", help="read current knowledge or save research progress")
    commands = research.add_subparsers(dest="research_command", required=True)
    current = commands.add_parser("current", help="bounded current brief; honor the entry read recipe")
    detail = commands.add_parser("detail", help="read exact fields from a pinned note")
    for command in (current, detail):
        command.add_argument("--entry", type=Path, required=True,
                             help="project entry, locator, or saved details pin JSON")
        command.add_argument("--branch")
        command.add_argument("--max-bytes", type=int, default=16384)
        command.add_argument("--verify-originals", action="store_true", default=None,
                             help="also check cited original bytes; does not print their bodies")
        command.set_defaults(handler=handle)
    current.add_argument("--recipe", type=Path, help="JSON read recipe; otherwise use the entry policy")
    detail.add_argument("--record-id", help="pin the record returned by current, even after its head changes")
    detail.add_argument("--view", choices=("selected", "index", "full"), default="selected")
    detail.add_argument("--select", action="append", help="literal JSON Pointer; repeat to batch fields")
    detail.add_argument("--offset", type=int, default=0)
    detail.add_argument("--limit", type=int, default=20)
    progress = commands.add_parser("progress", help="save explicit changed fields or a complete note")
    progress.add_argument("--entry", type=Path, required=True)
    body = progress.add_mutually_exclusive_group(required=True)
    body.add_argument("--patch", type=Path, help="JSON fields to replace; omitted fields remain unchanged")
    body.add_argument("--note", type=Path, help="complete note JSON, including task and branch")
    progress.add_argument("--branch", help="existing branch to patch; defaults to the current branch")
    progress.add_argument("--expected-record", help="reject changes to a different parent record")
    progress.add_argument("--publication-id", help="stable producer publication identity for replay")
    for role in ("evidence", "counterevidence"):
        progress.add_argument("--" + role, type=Path, action="append", default=[],
                             help="append an original file using existing identity checks; repeat as needed")
    progress.add_argument("--background", action="store_true", help="keep the current active branch")
    progress.set_defaults(handler=handle)
    work = commands.add_parser("work", help="read current executable work and feedback; never execute it")
    work.add_argument("--entry", type=Path, required=True)
    work.add_argument("--branch")
    work.set_defaults(handler=handle)
    method = commands.add_parser("method", help="describe an explicit method and resolve its source pin; never execute")
    saved = commands.add_parser("original", help="inspect one saved result or read explicit JSON/NPZ slices")
    for command in (method, saved):
        command.add_argument("--entry", type=Path, required=True)
        command.add_argument("--pointer", required=True, help="literal JSON Pointer inside the selected note")
        command.add_argument("--branch")
        command.add_argument("--max-bytes", type=int, default=16384)
        command.set_defaults(handler=handle)
    saved.add_argument("--selector", type=Path, help='JSON object: {"json":["/field"]} or {"npz":[{"key":"scores","slice":[[0,3],1]}]}')
    saved.add_argument("--bytes", action="store_true", help="read raw byte pages instead of metadata")
    saved.add_argument("--offset", type=int, default=0)
    saved.add_argument("--limit", type=int, default=20)
    commands.add_parser("runtime", help="actual interpreter and loaded reader paths and hashes").set_defaults(handler=handle)


def _object(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON object required: " + str(path))
    return value


def _entry_path(path):
    """Write through the existing one-hop locator, never replace that locator."""
    path = path.absolute()
    value = _object(path)
    if set(value) == {"entry"}:
        target = Path(value["entry"])
        path = target if target.is_absolute() else path.parent / target
    return path


def _read(args):
    from .notes import entry_value, read_current, read_entry

    if args.research_command == "current":
        recipe = _object(args.recipe) if args.recipe else None
        if args.verify_originals:
            recipe = dict(entry_value(args.entry).get("read_recipe") or {"view": "brief"}) if recipe is None else recipe
            recipe["verify_originals"] = True
        return read_current(args.entry, branch_id=args.branch, recipe=recipe,
                            max_bytes=args.max_bytes)
    entry = entry_value(args.entry)
    if args.record_id:
        # The same pin as read_current.details; do not retarget to a newer head.
        entry.pop("branches", None)
        entry.update(record_id=args.record_id, branch_id=args.branch or entry["branch_id"])
    return read_entry(entry, branch_id=args.branch, view=args.view, selectors=args.select,
                      offset=args.offset, limit=args.limit, max_bytes=args.max_bytes,
                      verify_originals=args.verify_originals)


def _progress(args):
    from .notes import entry_journal, entry_value, original, patch_progress, save_progress

    entry = _entry_path(args.entry)
    payload = _object(args.patch or args.note)
    if args.patch:
        return patch_progress(entry, payload, branch_id=args.branch,
            expected_record=args.expected_record, publication_id=args.publication_id,
            evidence=args.evidence, counterevidence=args.counterevidence,
            activate=not args.background)
    value = entry_value(entry)
    if args.note and args.branch and payload.get("branch_id") != args.branch:
        raise ValueError("--branch differs from the complete note")
    branch = payload.get("branch_id")
    heads = value.get("branches", {value["branch_id"]: value["record_id"]})
    note = payload
    for role in ("evidence", "counterevidence"):
        for path in getattr(args, role):
            ref = original(path)
            refs = note.setdefault(role, [])
            if ref not in refs:
                refs.append(ref)
    if args.expected_record:
        if args.expected_record != heads.get(branch):
            raise ValueError("entry changed; expected record is not the current publication parent")
        note["supersedes"] = [args.expected_record]
    journal = entry_journal(value, read_only=False)
    record_id = save_progress(journal, entry, note, activate=not args.background,
                              publication_id=args.publication_id)
    current = entry_value(entry)
    return {"ok": True, "entry": str(entry), "task_id": value["task_id"],
            "branch_id": branch, "record_id": record_id,
            "current_record_id": current["record_id"], "execution_performed": False}


def _runtime():
    import etalon
    from etalon.runtime.files import file_identity
    from . import notes

    modules = {"etalon": etalon, "etalon.research.cli": sys.modules[__name__],
               "etalon.research.notes": notes}
    return {"python": sys.executable, "python_version": sys.version.split()[0],
            "command": [sys.executable, "-m", "etalon", "research"],
            "modules": {name: {"path": str(Path(module.__file__).absolute()),
                                **file_identity(module.__file__)} for name, module in modules.items()}}


def handle(args: argparse.Namespace) -> int:
    try:
        if args.research_command == "method":
            from .notes import resolve_method
            result = resolve_method(args.entry, args.pointer, branch_id=args.branch, max_bytes=args.max_bytes)
        elif args.research_command == "original":
            from .notes import read_original
            if args.selector and args.bytes:
                raise ValueError("choose a selector or bytes")
            result = read_original(args.entry, args.pointer, branch_id=args.branch,
                selector="bytes" if args.bytes else _object(args.selector) if args.selector else None,
                offset=args.offset, limit=args.limit, max_bytes=args.max_bytes)
        elif args.research_command == "work":
            from .research_segment import read_work
            result = read_work(args.entry, branch_id=args.branch)
        else:
            result = (_runtime() if args.research_command == "runtime" else
                      _progress(args) if args.research_command == "progress" else _read(args))
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}},
                         ensure_ascii=False, separators=(",", ":")))
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0
