"""Environment diagnostics and explicit bulk-screen submissions."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from typing import Any


def register(sub: Any) -> None:
    doctor = sub.add_parser("doctor", help="check dependencies and execution readiness without running science")
    doctor.add_argument("--prism-python", help="interpreter in the PRISM/AmberTools environment")
    doctor.add_argument("--require", action="append", default=[],
                        choices=("planning", "active", "cascade", "quarry", "mcp", "prism-build-tools"))
    doctor.set_defaults(handler=handle)
    screen = sub.add_parser("screen", help="plan, submit and inspect a real bulk MolCascade screen")
    commands = screen.add_subparsers(dest="screen_command", required=True)
    for name in ("plan", "submit", "status", "export"):
        command = commands.add_parser(name)
        command.add_argument("--workspace", required=True)
        if name in {"plan", "submit"}:
            command.add_argument("--config", required=True)
            command.add_argument("--library", help="library override; omit for a flat pipeline")
            command.add_argument("--allow-copyleft", action="store_true")
        if name != "plan":
            command.add_argument("--run-id", required=True)
        if name == "export":
            command.add_argument("--output", required=True, help="new SDF/SMILES shortlist file")
        if name == "submit":
            command.add_argument("--plan-id", required=True, help="plan_id returned by screen plan")
            command.add_argument("--workers", type=int, default=1)
            command.add_argument("--device", action="append", default=[])
        command.set_defaults(handler=handle)


def handle(arguments: argparse.Namespace) -> int:
    try:
        with redirect_stdout(sys.stderr):
            if arguments.command == "doctor":
                from etalon.doctor import diagnose

                report = diagnose(prism_python=arguments.prism_python)
                success = all(report["readiness"][key] for key in arguments.require)
            else:
                from etalon.screening import plan_screen, screen_status, submit_screen

                if arguments.screen_command == "export":
                    from etalon.boundary.screen import Screen

                    report = Screen(arguments.workspace).export_shortlist(arguments.run_id, arguments.output)
                elif arguments.screen_command == "status":
                    report = screen_status(arguments.workspace, arguments.run_id)
                elif arguments.screen_command == "plan":
                    report = plan_screen(arguments.config, arguments.library, arguments.workspace,
                                         allow_copyleft=arguments.allow_copyleft)
                else:
                    report = submit_screen(arguments.config, arguments.library, arguments.workspace,
                        run_id=arguments.run_id, expected_plan_id=arguments.plan_id,
                        workers=arguments.workers, devices=tuple(arguments.device),
                        allow_copyleft=arguments.allow_copyleft)
                success = report.get("state") != "failed"
    except Exception as error:
        report, success = {"error": {"code": type(error).__name__, "message": str(error)}}, False
    print(json.dumps({"ok": success, **report}, indent=2, ensure_ascii=False, allow_nan=False))
    return 0 if success else 1
