"""Database evidence, candidate preparation and reviewed campaign ingress."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any


def register(sub: Any) -> None:
    parser = sub.add_parser("data", help="MolQuarry evidence and frozen CADD inputs")
    commands = parser.add_subparsers(dest="data_command", required=True)
    for name in ("sources", "plan", "run", "status", "prepare", "import-candidates",
                 "review-template", "import-assays", "attach-handoffs"):
        command = commands.add_parser(name)
        command.set_defaults(handler=handle)
        if name == "sources":
            command.add_argument("--source")
        if name in {"plan", "run"}:
            command.add_argument("--request", required=True, help="JSON request file")
            command.add_argument("--max-requests", type=int, default=100)
            command.add_argument("--max-bytes", type=int, default=50_000_000)
            command.add_argument("--max-seconds", type=float, default=300)
        if name in {"run", "prepare", "attach-handoffs"}:
            command.add_argument("--workspace", required=True)
        if name in {"run", "prepare"}:
            command.add_argument("--run-id", required=True)
        if name == "run":
            command.add_argument("--plan-id", help="refuse changed inputs/settings since data plan")
        if name in {"status", "prepare", "import-candidates", "review-template", "import-assays"}:
            command.add_argument("--snapshot", required=True)
        if name == "prepare":
            command.add_argument("--id-field")
            command.add_argument("--smiles-field")
            command.add_argument("--allow-partial", action="store_true")
            command.add_argument("--identity-policy", help="JSON policy file; default matches campaign.compose")
        if name in {"import-candidates", "import-assays", "attach-handoffs"}:
            command.add_argument("--database", required=True, help="existing configured campaign journal")
        if name == "review-template":
            command.add_argument("--endpoint", required=True)
            command.add_argument("--protocol", required=True)
        if name == "import-assays":
            command.add_argument("--review", required=True)
        if name == "attach-handoffs":
            command.add_argument("--artifact-id", required=True)
            command.add_argument("--rationale", required=True)
        if name in {"import-candidates", "attach-handoffs"}:
            command.add_argument("--candidate", action="append")


def existing_store(path: str | Path) -> Any:
    from etalon.active.store import CampaignStore

    if not Path(path).is_file():
        raise FileNotFoundError("configure a campaign journal before importing data")
    store = CampaignStore(path)
    store.configuration()
    return store


def handle(arguments: argparse.Namespace) -> int:
    from etalon.boundary.quarry import DataBudget, describe
    from etalon.data.artifacts import status, summary
    from etalon.data.ingress import attach_handoffs, import_assays, review_template
    from etalon.data.library import import_candidates, prepare_library
    from etalon.data.service import plan_data, run_data

    def read(path: str) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    try:
        with redirect_stdout(sys.stderr):
            name = arguments.data_command
            if name == "sources":
                report = describe(arguments.source)
            elif name in {"plan", "run"}:
                budget = DataBudget(arguments.max_requests, arguments.max_bytes, arguments.max_seconds)
                request = read(arguments.request)
                report = (plan_data(request, budget=budget) if name == "plan" else run_data(
                    request, Path(arguments.workspace), run_id=arguments.run_id, budget=budget,
                    expected_plan_id=arguments.plan_id))
            elif name == "status":
                report = status(Path(arguments.snapshot))
            elif name == "prepare":
                report = prepare_library(Path(arguments.snapshot), Path(arguments.workspace),
                    run_id=arguments.run_id, id_field=arguments.id_field,
                    smiles_field=arguments.smiles_field, allow_partial=arguments.allow_partial,
                    identity_policy=read(arguments.identity_policy) if arguments.identity_policy else None)
            elif name == "review-template":
                report = review_template(Path(arguments.snapshot), endpoint_id=arguments.endpoint,
                                         protocol=arguments.protocol)
            elif name == "import-candidates":
                report = import_candidates(existing_store(arguments.database), Path(arguments.snapshot),
                                            candidate_ids=arguments.candidate)
            elif name == "import-assays":
                report = import_assays(existing_store(arguments.database), Path(arguments.snapshot),
                                        read(arguments.review))
            else:
                report = attach_handoffs(existing_store(arguments.database), Path(arguments.workspace),
                    arguments.artifact_id, rationale=arguments.rationale, candidate_ids=arguments.candidate)
        success = report.get("state") != "failed"
    except Exception as error:
        report, success = {"error": {"code": type(error).__name__, "message": str(error)}}, False
    payload = report if success and arguments.data_command == "review-template" else {"ok": success, **summary(report)}
    print(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False))
    return 0 if success else 1
