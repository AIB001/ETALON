"""Small JSON commands over the existing research-note APIs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def register(sub):
    research = sub.add_parser("research", help="read current knowledge or save research progress")
    commands = research.add_subparsers(dest="research_command", required=True)
    advisory = commands.add_parser("advice", help="host-owned side advice over the existing entries")
    advisory.add_argument("--entry", type=Path, required=True)
    advisory.add_argument("--action", choices=("prepare", "bind", "report", "collect", "decide"), required=True)
    advisory.add_argument("--topic", required=True)
    advisory.add_argument("--review-key", required=True)
    advisory.add_argument("--payload", type=Path, help="compact JSON for the selected action")
    advisory.set_defaults(handler=handle)
    fork = commands.add_parser("fork-memory", help="create independent working memory sharing original results")
    fork.add_argument("--entry", type=Path, required=True)
    fork.add_argument("--destination", type=Path, required=True)
    fork.add_argument("--scope", required=True)
    fork.add_argument("--question", required=True)
    fork.set_defaults(handler=handle)
    resources = commands.add_parser("resources", help="page methods, skills and conditional knowledge in a published entry")
    resources.add_argument("--entry", type=Path, required=True)
    resources.add_argument("--kind", action="append")
    resources.add_argument("--query", type=Path, help="JSON task conditions and optional critical_conditions, definitions, available_stages")
    resources.add_argument("--resource-id", action="append", help="select exact resource and associated feedback; repeat as needed")
    resources.add_argument("--family", action="append", help="scope discovery to declared operation families; unclassified resources remain outside this view")
    resources.add_argument("--cursor")
    resources.add_argument("--limit", type=int, default=20)
    resources.add_argument("--max-bytes", type=int, default=16384)
    resources.set_defaults(handler=handle)
    feedback = commands.add_parser("feedback", help="read conditional use feedback from a fixed note")
    feedback.add_argument("--entry", type=Path, required=True)
    feedback.add_argument("--resource-id", required=True)
    feedback.add_argument("--query", type=Path, help="JSON task conditions")
    feedback.add_argument("--branch")
    feedback.add_argument("--resource-version")
    feedback.add_argument("--offset", type=int, default=0)
    feedback.add_argument("--limit", type=int, default=20)
    feedback.add_argument("--max-bytes", type=int, default=8192)
    feedback.set_defaults(handler=handle)
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
    body.add_argument("--resource-feedback", type=Path, help="JSON exact resource/version/parts, usage_id, conditions, outcome, summary and measured cost")
    progress.add_argument("--branch", help="existing branch to patch; defaults to the current branch")
    progress.add_argument("--expected-record", help="reject changes to a different parent record")
    progress.add_argument("--publication-id", help="stable producer publication identity for replay")
    for role in ("evidence", "counterevidence"):
        progress.add_argument("--" + role, type=Path, action="append", default=[],
                             help="append an original file; relative paths use the supplied entry/locator directory, not cwd; repeat as needed")
    progress.add_argument("--background", action="store_true", help="keep the current active branch")
    progress.set_defaults(handler=handle)
    work = commands.add_parser("work", help="read current executable work and feedback; never execute it")
    work.add_argument("--entry", type=Path, required=True)
    work.add_argument("--branch")
    work.set_defaults(handler=handle)
    method = commands.add_parser("method", help="describe an explicit method and resolve its source pin; never execute")
    method.add_argument("--invocation", type=Path, help="JSON request_id/limits/inputs; return an action request without running it")
    method.add_argument("--query", type=Path, help="optional JSON task conditions for advisory applicability")
    method_save = commands.add_parser("method-save", help="publish one callable method in the existing entry catalog")
    method_save.add_argument("--entry", type=Path, required=True)
    method_save.add_argument("--resource-id", required=True)
    method_save.add_argument("--descriptor", type=Path, required=True,
                             help="JSON arguments/summary/conditions and optional evidence/counterevidence paths")
    method_save.add_argument("--branch")
    method_save.add_argument("--publication-id")
    method_save.set_defaults(handler=handle)
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
    from .notes import entry_evidence_paths, entry_journal, entry_value, original, patch_progress, save_progress

    entry = _entry_path(args.entry)
    if args.resource_feedback:
        if args.expected_record or args.publication_id or args.background:
            raise ValueError("resource feedback uses its usage_id and normal publication; send other changes separately")
        from .resource_selection import record_resource_feedback
        payload = _object(args.resource_feedback)
        inner_branch = payload.pop("branch_id", None)
        if args.branch and inner_branch and args.branch != inner_branch:
            raise ValueError("resource feedback branch differs from --branch")
        for key in ("evidence", "counterevidence"):
            payload[key] = [*payload.get(key, []), *getattr(args, key)]
        return record_resource_feedback(args.entry, **payload, branch_id=args.branch or inner_branch)
    payload = _object(args.patch or args.note)
    if args.patch:
        return patch_progress(args.entry, payload, branch_id=args.branch,
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
        for path in entry_evidence_paths(args.entry, getattr(args, role)):
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
        if args.research_command == "advice":
            from .advice import advice
            result = advice(args.entry, args.action, args.topic, args.review_key,
                            _object(args.payload) if args.payload else None)
        elif args.research_command == "fork-memory":
            from .notes import fork_memory
            result = fork_memory(args.entry, args.destination, memory_scope=args.scope, question=args.question)
        elif args.research_command == "resources":
            from .resource_catalog import build_resource_catalog
            result = build_resource_catalog(entry=args.entry, kinds=args.kind, cursor=args.cursor,
                limit=args.limit, max_bytes=args.max_bytes, resource_ids=args.resource_id,
                query=_object(args.query) if args.query else None, families=args.family)
        elif args.research_command == "feedback":
            from .resource_selection import read_resource_feedback
            result = read_resource_feedback(args.entry, args.resource_id,
                query=_object(args.query) if args.query else None, branch_id=args.branch,
                resource_version=args.resource_version, offset=args.offset,
                limit=args.limit, max_bytes=args.max_bytes)
        elif args.research_command == "method":
            from .saved_methods import resolve_saved_method
            result = resolve_saved_method(args.entry, args.pointer, branch_id=args.branch,
                invocation=_object(args.invocation) if args.invocation else None, max_bytes=args.max_bytes,
                query=_object(args.query) if args.query else None)
        elif args.research_command == "method-save":
            from .saved_methods import publish_method
            result = publish_method(args.entry, args.resource_id, **_object(args.descriptor),
                branch_id=args.branch, publication_id=args.publication_id)
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
