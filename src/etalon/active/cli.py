"""Offline execution and read-only inspection; no implicit live compute from the CLI."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from etalon.active.runner import ActiveCampaign
from etalon.active.store import CampaignStore, StateError


def register(sub: Any) -> None:
    active = sub.add_parser("active", help="persistent active learning: offline replay, benchmark and inspection")
    commands = active.add_subparsers(dest="active_command", required=True)
    catalogue = commands.add_parser("components", help="discover MolCascade criteria and individual plugin contracts")
    catalogue.set_defaults(handler=handle)
    for name in ("status", "plan", "export", "recommend", "protocols", "searches"):
        command = commands.add_parser(name, help=f"read-only campaign {name}")
        command.add_argument("--database", required=True, type=Path)
        command.add_argument("--output", type=Path, help="write JSON exclusively; an existing file is never overwritten")
        command.set_defaults(handler=handle)
    replay = commands.add_parser("replay", help="run/resume a sealed offline oracle table; no live compute")
    replay.add_argument("--manifest", required=True, type=Path)
    replay.add_argument("--workspace", required=True, type=Path)
    replay.add_argument("--rounds", type=int, default=10)
    replay.add_argument("--output", type=Path)
    replay.set_defaults(handler=handle)
    protocol_benchmark = commands.add_parser("protocol-benchmark", help="synthetic finite protocol-search ablation; no real computation")
    protocol_benchmark.add_argument("--seeds", default="0,1,2,3,4")
    protocol_benchmark.add_argument("--budget", type=float, default=6)
    protocol_benchmark.add_argument("--max-trials", type=int, default=16)
    protocol_benchmark.add_argument("--output", type=Path, help="save JSON exclusively; never overwrite")
    protocol_benchmark.set_defaults(handler=handle)
    stopping = commands.add_parser("protocol-stopping-benchmark", help="synthetic economic-stopping ablation; no live tools")
    stopping.add_argument("--seeds", default="0,1,2,3,4")
    stopping.add_argument("--budget", type=float, default=6)
    stopping.add_argument("--max-trials", type=int, default=16)
    stopping.add_argument("--opportunity-costs", default="0.02,0.1,0.3", help="explicit panel-score units per cost unit")
    stopping.add_argument("--output", type=Path, help="save JSON exclusively; never overwrite")
    stopping.set_defaults(handler=handle)
    for name in ("demo", "benchmark", "decision-benchmark"):
        command = commands.add_parser(name, help="synthetic engineering smoke test; NOT a CADD efficacy result")
        command.add_argument("--workspace", required=True, type=Path)
        command.add_argument("--budget", type=float, default=120)
        command.add_argument("--size", type=int, default=64)
        command.add_argument("--rounds", type=int, default=200 if name == "decision-benchmark" else 40)
        command.add_argument("--output", type=Path, help="persist the JSON evidence without overwriting existing files")
        if name == "demo":
            command.add_argument("--seed", type=int, default=7)
            command.add_argument("--policy", choices=("random", "greedy", "ucb", "cost_only", "cost_aware", "mf_kg", "decision_aware"), default="cost_aware")
        else:
            command.add_argument("--seeds", default="0,1,2,3,4")
        command.set_defaults(handler=handle)


def handle(arguments: argparse.Namespace) -> int:
    try:
        output = getattr(arguments, "output", None)
        if output is not None and output.exists():
            raise FileExistsError(f"refusing to overwrite {output}")
        result = _run(arguments)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, indent=2, allow_nan=False)
                handle.write("\n")
            result = {"output": str(output.resolve()),
                      "summary": result.get("summary", {"stop_reason": result.get("stop_reason")})}
    except (OSError, ValueError, KeyError, TypeError, StateError, sqlite3.Error, ImportError) as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    command = arguments.active_command
    if command == "components":
        from etalon.campaign.design import catalogue

        return catalogue()
    if command in {"status", "plan", "export", "recommend", "protocols", "searches"}:
        store = CampaignStore(arguments.database, read_only=True)
        if command == "status":
            return store.status()
        if command == "recommend":
            return ActiveCampaign(store).recommend()
        if command == "protocols":
            from etalon.active.protocols import ProtocolRegistry, protocol_limits

            registry = ProtocolRegistry(store)
            _, endpoints = store.configuration()
            proposals = registry.proposals()
            return {"proposals": proposals, "reports": [registry.report(p["id"]) for p in proposals],
                    "query_limits": protocol_limits(store, endpoints, store.observations()),
                    "bound_recipes": {key: recipe.protocol_id for key, recipe in registry.recipes().items()}}
        if command == "searches":
            from etalon.active.proposer import ProtocolSearch

            search = ProtocolSearch(store)
            records = search.searches()
            return {"searches": records, "plans": [search.plan(record["id"]) for record in records]}
        if command == "export":
            return store.export()
        return ActiveCampaign(store).inspect()
    if command == "protocol-stopping-benchmark":
        from etalon.active.protocol_stopping_benchmark import protocol_stopping_benchmark

        return protocol_stopping_benchmark(seeds=[int(s) for s in arguments.seeds.split(",")],
                                          budget=arguments.budget, max_trials=arguments.max_trials,
                                          opportunity_costs=[float(s) for s in arguments.opportunity_costs.split(",")])
    if command == "protocol-benchmark":
        from etalon.active.protocol_benchmark import protocol_benchmark

        return protocol_benchmark(seeds=[int(s) for s in arguments.seeds.split(",")],
                                  budget=arguments.budget, max_trials=arguments.max_trials)
    from etalon.active.replay import from_manifest, synthetic_manifest

    if arguments.rounds < 1:
        raise ValueError("rounds must be positive")
    if command in {"benchmark", "decision-benchmark"}:
        if command == "benchmark":
            from etalon.active.benchmark import benchmark
        else:
            from etalon.active.decision_benchmark import decision_benchmark as benchmark

        return benchmark(arguments.workspace, seeds=[int(s) for s in arguments.seeds.split(",")],
                         budget=arguments.budget, size=arguments.size, rounds=arguments.rounds)
    manifest = (json.loads(arguments.manifest.read_text(encoding="utf-8")) if command == "replay" else
                synthetic_manifest(seed=arguments.seed, size=arguments.size,
                                   budget=arguments.budget, policy=arguments.policy))
    campaign = from_manifest(manifest, arguments.workspace / "campaign.sqlite")
    result = campaign.run(max_rounds=arguments.rounds)
    return {"mode": "offline_replay", "database": str(campaign.store.path),
            "stop_reason": result["stop_reason"], "new_rounds": len(result["rounds"]),
            "state": result["state"]}
