"""Two real independent PRISM seeds -> engineering feedback -> plan only.

Run in the frozen ETALON environment:
  python examples/prism_round1.py --config /absolute/config.json --workspace /absolute/new/run
Dependencies: ETALON [active] (numpy/scipy); PRISM runs in its separately specified Python/cwd.
No fixture values, implicit PRISM import, third simulation, or dependency installation.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore, Candidate, Endpoint
from etalon.active.prism import PrismV1Executor
from etalon.active.runner import ExecutionPending
from etalon.active.schema import Action, Evaluation, canonical, digest
from etalon.authority.grant import authorize, require
from etalon.boundary.prism_v1 import CLITransport, fields, file_identity, validate_request
from etalon.faults.preflight import check_record


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(canonical(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if Path(name).exists():
            Path(name).unlink()


def inspect(campaign: ActiveCampaign) -> dict:
    model, choices, reason = campaign.plan()
    ids = sorted(campaign.store.candidates())
    mean, sd = model.predict(ids, model.objective)
    return {"model": model.snapshot(), "training_action_ids": model.training_action_ids,
            "prediction": {key: {"mean": float(m), "sd": float(s)} for key, m, s in zip(ids, mean, sd)},
            "choices": [vars(c) for c in choices], "stop_reason": reason,
            "observations": campaign.store.observations(), "budget": campaign.store.balance()}


def run(config: dict, workspace: Path, *, resume: bool = False) -> dict:
    fields(config, {"request_template", "transport", "job_root", "receptor_path", "handoff",
                    "seeds", "budget", "quote_wall_seconds"}, {"wait_seconds", "poll_seconds"})
    template = config["request_template"]
    validate_request(template)
    if len(config["seeds"]) != 2 or len(set(config["seeds"])) != 2:
        raise ValueError("round1 requires exactly two distinct authorized seeds")
    row = config["handoff"]
    if row.get("parent_smiles") != "c1ccccc1":
        raise ValueError("round1 driver is explicitly limited to the real benzene candidate")
    endpoint = Endpoint(template["endpoint_id"], "5JWT-bounded-engineering", "mean_residue_contacts",
                        "count", template["protocol_id"], config["quote_wall_seconds"], direction="maximize",
                        requires_handoff=True, max_replicates=3, noise=.1)
    # Fixed feature identifies one molecule; seeds never create additional molecular identities.
    candidate = Candidate(template["candidate_id"], "c1ccccc1", (1.,), scaffold="benzene",
                          source="explicit bounded PRISM input", handoff=row)
    workspace = workspace.resolve()
    store = CampaignStore(workspace / "campaign.sqlite")
    spec = CampaignSpec(endpoint.id, config["budget"], "wall_second", "single-known-molecule/1",
                        batch_size=1, seed=20261018, bootstrap=2, explore_fraction=0)
    store.configure(spec, [endpoint])
    store.add_candidates([candidate])
    executor = PrismV1Executor(store, CLITransport(**config["transport"]), request_template=template,
                               job_root=config["job_root"], receptor_path=config["receptor_path"],
                               seeds=config["seeds"], wait_seconds=config.get("wait_seconds", 3600),
                               poll_seconds=config.get("poll_seconds", 2))
    # Unknown toolchain shim remains explicitly unevaluable; never claim a shim is installed.
    campaign = ActiveCampaign(store, executor, receptor_path=config["receptor_path"])
    version = {"python": sys.executable, "python_version": sys.version,
               "etalon_module": __import__("etalon").__file__,
               "driver": {"path": str(Path(__file__).resolve()), **file_identity(__file__)},
               "sources": {str(p.relative_to(Path(__import__('etalon').__file__).parent)): file_identity(p)
                           for p in sorted(Path(__import__('etalon').__file__).parent.rglob('*.py'))},
               "config_sha256": digest(config)}
    if not (workspace / "environment.json").exists():
        write_json(workspace / "environment.json", version)
    elif json.loads((workspace / "environment.json").read_text()) != version:
        raise ValueError("driver environment/source/config changed; preserve old run and use its frozen version")
    for value in store.actions():
        if value["status"] not in {"running", "reserved"}:
            continue
        action = Action(**{k: value[k] for k in Action.__dataclass_fields__})
        checks = check_record(row, receptor_path=Path(config["receptor_path"]))
        grants = authorize([row], receptor_path=Path(config["receptor_path"]))
        grant = require(row, grants.grants, receptor_path=Path(config["receptor_path"]))
        if resume:
            executor.resume(action, candidate, endpoint, grant)
        if value["status"] == "reserved":
            store.start_action(action.id)
        result = executor(action, candidate, endpoint, grant)
        result = replace(result, checks=checks + result.checks,
                         provenance={**result.provenance, "authorization": grant.as_dict(),
                                     "endpoint_protocol": endpoint.protocol})
        store.resolve(action.id, result, idempotent=True)
    if any(r["status"] == "running" for r in store.rounds()):
        store.recover_idle_rounds(reason="same single driver reconciled all persisted external actions")
    before_path = workspace / "before.json"
    if not before_path.exists():
        write_json(before_path, inspect(campaign))
    while len(store.actions()) < 2:
        outcome = campaign.run_round()
        write_json(workspace / f"after-{len(store.actions())}.json", inspect(campaign))
        if not outcome["actions"] or outcome["stop_reason"] != "ready":
            break
    after = inspect(campaign)
    state = store.snapshot()
    # Freeze actions/budget/eligibility and remove only observations: isolate data feedback
    # from candidate removal. This does not overwrite the real journal or dispatch work.
    counterfactual_model, counterfactual_choices, reason = campaign._plan_snapshot({**state, "observations": []})
    duplicate_reads = []
    for observation in store.observations():
        raw = observation["result"]["provenance"].get("result")
        if raw is None:
            continue
        request = observation["result"]["provenance"]["request"]
        action_dict = next(a for a in store.actions() if a["id"] == observation["action_id"])
        action = Action(**{k: action_dict[k] for k in Action.__dataclass_fields__})
        received = executor.transport.call("result", job=raw["job_directory"])
        executor.evaluation(action, request, received)
        before_balance = store.balance()
        store.resolve(action.id, Evaluation.from_dict(observation["result"]), idempotent=True)
        duplicate_reads.append({"action_id": action.id, "budget_unchanged": before_balance == store.balance()})
    report = {"scope": "engineering_only", "affinity_or_design_optimization_complete": False,
              "unique_molecules": 1, "independent_seed_repetitions": len(store.observations(admitted_only=True)),
              "before": json.loads(before_path.read_text()), "after": after,
              "next_plan_executed": False, "duplicate_reads": duplicate_reads,
              "fixed_history_without_observations": {"model": counterfactual_model.snapshot(),
                  "training_action_ids": counterfactual_model.training_action_ids,
                  "choices": [vars(c) for c in counterfactual_choices], "stop_reason": reason}}
    write_json(workspace / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="explicitly resume existing needs_resume actions")
    args = parser.parse_args()
    try:
        report = run(json.loads(args.config.read_text(encoding="utf-8")), args.workspace, resume=args.resume)
    except ExecutionPending as error:
        print(json.dumps({"status": "pending", "detail": str(error), "workspace": str(args.workspace)}))
        raise SystemExit(3) from error
    print(json.dumps({"report": str(args.workspace.resolve() / "report.json"),
                      "admitted": report["independent_seed_repetitions"],
                      "budget": report["after"]["budget"], "next_plan_executed": False}))


if __name__ == "__main__":
    main()
