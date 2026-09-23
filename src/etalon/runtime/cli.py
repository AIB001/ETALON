"""JSON-first CLI for durable workflows and explicitly registered scientific executors."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any


def register(sub: Any) -> None:
    workflows = sub.add_parser("workflow", help="plan, execute and inspect durable CADD workflows")
    commands = workflows.add_subparsers(dest="workflow_command", required=True)
    for name in ("capabilities", "config", "plan", "submit", "status", "observe", "advance",
                 "cancel", "reconcile", "artifact", "active-plan", "active-submit"):
        command = commands.add_parser(name)
        command.set_defaults(handler=handle, runtime_family="workflow")
        command.add_argument("--output", type=Path, help="save JSON exclusively; never overwrite")
        if name in {"config", "plan", "submit"}:
            command.add_argument("--config", type=Path, required=True, help="JSON configuration or workflow spec")
        if name not in {"capabilities", "plan", "active-plan"}:
            command.add_argument("--workspace", type=Path, required=True)
        if name not in {"capabilities", "config", "plan", "active-plan"}:
            command.add_argument("--job-id", required=True)
        if name in {"submit", "active-submit"}:
            command.add_argument("--plan-id", required=True)
        if name == "advance":
            command.add_argument("--proposal", type=Path, required=True, help="strict decision JSON")
        if name in {"cancel", "reconcile"}:
            command.add_argument("--reason", required=True)
        if name == "reconcile":
            command.add_argument("--resume", action="store_true")
            command.add_argument("--settlements", type=Path, help="JSON failure settlements with evidence")
        if name == "artifact":
            command.add_argument("--node-id", required=True)
            command.add_argument("--kind", choices=("result", "snapshot", "screen"), default="result")
            command.add_argument("--member", default="")
            command.add_argument("--artifact-id", default="")
            command.add_argument("--contract-id", default="")
            command.add_argument("--offset", type=int, default=0)
            command.add_argument("--limit", type=int, default=100)
        if name in {"active-plan", "active-submit"}:
            command.add_argument("--database", type=Path, required=True)
            command.add_argument("--max-rounds", type=int, default=1)
            command.add_argument("--min-new-admitted", type=int, default=1)
            command.add_argument("--max-seconds", type=float, default=3600)

    executors = sub.add_parser("executor", help="prepare and register immutable scientific executors")
    commands = executors.add_subparsers(dest="executor_command", required=True)
    for name in ("prepare", "register", "list"):
        command = commands.add_parser(name)
        command.set_defaults(handler=handle, runtime_family="executor")
        command.add_argument("--output", type=Path, help="save JSON exclusively; never overwrite")
        if name in {"prepare", "register"}:
            command.add_argument("--config", type=Path, required=True,
                                 help="JSON kind/configuration/endpoint, or prepared executor for register")
        if name in {"register", "list"}:
            command.add_argument("--database", type=Path, required=True)
        if name == "register":
            command.add_argument("--rationale", required=True)


def _read(path: Path) -> dict:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("CLI JSON input exceeds 4 MiB")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    json.dumps(value, allow_nan=False)
    if not isinstance(value, dict):
        raise ValueError("CLI JSON input must be an object")
    return value


def _execute(args: argparse.Namespace) -> dict:
    from etalon.runtime import service
    from etalon.runtime.artifacts import configure
    from etalon.runtime.executors import (
        prepare_executor,
        register_executor,
        registered_executors,
    )

    if args.runtime_family == "executor":
        if args.executor_command == "list":
            return {"executors": registered_executors(args.database.absolute())}
        value = _read(args.config)
        if args.executor_command == "prepare":
            if set(value) != {"kind", "configuration", "endpoint"}:
                raise ValueError("executor prepare needs exactly kind, configuration and endpoint")
            return {"executor": prepare_executor(**value)}
        if set(value) == {"ok", "executor"} and value["ok"] is True:
            value = value["executor"]
        return register_executor(args.database.absolute(), value, args.rationale)
    name = args.workflow_command
    if name == "capabilities":
        from etalon.runtime.operations import describe

        return {"operations": describe(), "workflow_schema": "etalon-workflow/1",
                "controller_modes": ["ordered", "advisor", "external"]}
    if name == "config":
        return configure(args.workspace.absolute(), _read(args.config))
    if name in {"plan", "submit"}:
        spec = _read(args.config)
        if "plan_id" in spec and isinstance(spec.get("spec"), dict):
            spec = spec["spec"]
        if name == "plan":
            return service.plan(spec)
        return {"job": service.submit(spec, args.workspace.absolute(), job_id=args.job_id,
                                      expected_plan_id=args.plan_id)}
    if name in {"active-plan", "active-submit"}:
        from etalon.runtime.api import active_execution_plan, active_submit

        options = {"max_rounds": args.max_rounds, "min_new_admitted": args.min_new_admitted,
                   "max_seconds": args.max_seconds}
        if name == "active-plan":
            return active_execution_plan(args.database.absolute(), **options)
        return {"job": active_submit(args.database.absolute(), args.workspace.absolute(),
                                     job_id=args.job_id, expected_plan_id=args.plan_id, **options)}
    root = args.workspace.absolute()
    if name == "status":
        return {"job": service.status(root, args.job_id)}
    if name == "observe":
        return service.observe(root, args.job_id)
    if name == "advance":
        return {"job": service.advance(root, args.job_id, _read(args.proposal))}
    if name == "cancel":
        return {"job": service.cancel(root, args.job_id, reason=args.reason)}
    if name == "reconcile":
        return {"job": service.reconcile(root, args.job_id, reason=args.reason, resume=args.resume,
                                        settlements=_read(args.settlements) if args.settlements else None)}
    return service.read_artifact(root, args.job_id, args.node_id, kind=args.kind, member=args.member,
                                 artifact_id=args.artifact_id, contract_id=args.contract_id,
                                 offset=args.offset, limit=args.limit)


def handle(arguments: argparse.Namespace) -> int:
    try:
        output = arguments.output
        if output is not None and (output.exists() or output.is_symlink()):
            raise FileExistsError(f"refusing to overwrite {output}")
        with redirect_stdout(sys.stderr):
            report = _execute(arguments)
        payload = {"ok": True, **report}
        rendered = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                handle.write(rendered + "\n")
    except Exception as error:
        print(json.dumps({"ok": False, "error": {"code": type(error).__name__, "message": str(error)}},
                         ensure_ascii=False))
        return 1
    print(rendered)
    return 0
