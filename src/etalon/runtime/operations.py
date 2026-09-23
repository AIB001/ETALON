"""Finite operation registry backed by existing ETALON services and validators.

This is the execution/verification half of the controller. Neither a model's
completion claim nor a successful Python return establishes a scientific result.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from etalon.active.schema import canonical, digest
from etalon.active.store import CampaignStore, StateError
from etalon.data.artifacts import file_hash, read_snapshot
from etalon.runtime.schema import absolute, bounded_int, fields, references

_OPERATIONS = {
    "data.acquire": ({"request", "budget"}, {"allow_partial", "min_records"}),
    "data.prepare": ({"snapshot"}, {"id_field", "smiles_field", "identity_policy", "allow_partial", "min_records"}),
    "campaign.create": ({"spec", "endpoints", "executors"}, set()),
    "campaign.import": ({"database", "snapshot"}, {"candidate_ids"}),
    "campaign.assays": ({"database", "snapshot", "review"}, set()),
    "campaign.handoffs": ({"database", "workspace", "artifact_id", "rationale"}, {"candidate_ids"}),
    "screen.run": ({"config_path", "library_path"}, {"workers", "devices", "allow_copyleft"}),
    "screen.export": ({"workspace", "run_id"}, set()),
    "active.run": ({"database", "max_rounds"}, {"min_new_admitted"}),
}


def describe() -> dict:
    return {key: {"required": sorted(required), "optional": sorted(optional)}
            for key, (required, optional) in _OPERATIONS.items()}


def validate_arguments(operation: str, arguments: dict, *, deferred: bool = False) -> None:
    if operation not in _OPERATIONS:
        raise ValueError("operation is not in the installed ETALON registry")
    fields(arguments, *_OPERATIONS[operation])
    if deferred and references(arguments):
        return
    if references(arguments):
        raise ValueError("unresolved workflow output reference")
    for key in ("database", "snapshot", "workspace", "config_path", "library_path"):
        if key in arguments:
            absolute(arguments[key])
    for key in ("allow_partial", "allow_copyleft"):
        if key in arguments and type(arguments[key]) is not bool:
            raise ValueError(f"{key} must be an explicit boolean")
    for key, low, high in (("max_rounds", 1, 100), ("workers", 1, 256),
                           ("min_records", 0, 1_000_000_000), ("min_new_admitted", 1, 5000)):
        if key in arguments:
            bounded_int(arguments[key], key, low, high)
    if operation == "data.acquire":
        from etalon.boundary.quarry import DataBudget

        DataBudget(**arguments["budget"])
    if operation == "campaign.create":
        from etalon.active.schema import CampaignSpec, Endpoint

        CampaignSpec(**arguments["spec"])
        if not isinstance(arguments["endpoints"], list) or not isinstance(arguments["executors"], list):
            raise ValueError("endpoints and prepared executors must be explicit lists")
        for endpoint in arguments["endpoints"]:
            Endpoint(**endpoint)


def input_hashes(value: Any) -> dict[str, str]:
    """Pin immutable input files and sealed snapshots; active SQLite journals remain mutable."""
    result = {}
    if isinstance(value, dict):
        if "$ref" in value:
            return result
        for key, item in value.items():
            if key in {"database", "workspace", "python"}:
                continue
            if isinstance(item, str) and (key.endswith("_path") or key in {"path", "input_sdf"}):
                path = absolute(item)
                if not path.is_file():
                    raise FileNotFoundError(path)
                result[str(path)] = file_hash(path)
                if key == "config_path":
                    from etalon.active.cascade import _configuration_input_paths

                    try:
                        configuration = json.loads(path.read_text())
                    except json.JSONDecodeError:
                        import yaml

                        configuration = yaml.safe_load(path.read_text())
                    for resource in _configuration_input_paths(configuration):
                        if not resource.is_file():
                            raise FileNotFoundError(resource)
                        result[str(resource)] = file_hash(resource)
            elif key in {"local_catalogs", "files"} and isinstance(item, list):
                for name in item:
                    resource = absolute(name)
                    if not resource.is_file():
                        raise FileNotFoundError(resource)
                    result[str(resource)] = file_hash(resource)
            elif key == "snapshot" and isinstance(item, str):
                snapshot = read_snapshot(absolute(item))
                result[str(Path(snapshot["snapshot"]) / "snapshot.json")] = file_hash(Path(snapshot["snapshot"]) / "snapshot.json")
            result.update(input_hashes(item))
    elif isinstance(value, list):
        for item in value:
            result.update(input_hashes(item))
    return result


def check_inputs(inputs: dict) -> None:
    for name, sha in inputs.items():
        if not Path(name).is_file() or file_hash(Path(name)) != sha:
            raise StateError(f"planned input changed or disappeared: {name}")


def preflight(operation: str, parameters: dict, resources: dict) -> dict:
    validate_arguments(operation, parameters)
    inputs = input_hashes(parameters)
    if operation == "data.acquire":
        from etalon.boundary.quarry import DataBudget
        from etalon.data.service import plan_data

        budget = DataBudget(**parameters["budget"])
        if (resources.get("http_requests", 0) < budget.max_requests
                or resources.get("http_bytes", 0) < budget.max_bytes):
            raise ValueError("data steps reserve their complete http_requests/http_bytes allowances")
        plan = plan_data(parameters["request"], budget=budget)
        inputs.update({entry["path"]: entry["sha256"] for entry in plan["inputs"]})
        return {"inputs": inputs, "data_plan_id": plan["plan_id"]}
    if operation == "active.run":
        from etalon.runtime.executors import registered_executors

        store = CampaignStore(absolute(parameters["database"]), read_only=True)
        state = store.status()
        if state["pending"] or any(r["status"] == "running" for r in state["rounds"]):
            raise StateError("active journal needs result/cost reconciliation before dispatch")
        registry = registered_executors(store.path)
        if any(e["queryable"] and e["id"] not in registry for e in state["endpoints"]):
            raise ValueError("every queryable endpoint needs a registered executor")
        unit = "campaign:" + state["spec"]["cost_unit"]
        if resources.get(unit, 0) + 1e-12 < state["balance"]["remaining"]:
            raise ValueError(f"reserve the campaign's remaining allowance in {unit}; configure a smaller campaign budget if needed")
        return {"inputs": inputs, "campaign_state": state, "registry_hash": digest(registry)}
    if operation == "screen.run":
        if not resources:
            raise ValueError("screen steps require an explicit resource quote; GPU cost is not inferred from wall time")
        from etalon.screening import _prepare

        with tempfile.TemporaryDirectory(prefix="etalon-screen-preflight-") as scratch:
            prepared, _, _ = _prepare(params_config := parameters["config_path"], parameters["library_path"], scratch,
                                      allow_copyleft=parameters.get("allow_copyleft", False))
        inputs.update(prepared["request"]["inputs"])
        return {"inputs": inputs, "screen_inputs": prepared["request"]["inputs"],
                "screen_revision": prepared["request"]["revision_id"], "configuration": params_config}
    return {"inputs": inputs}


def execute(operation: str, params: dict, ctx: Any) -> dict:
    """Execute exactly once in an operation-owned workspace. The caller persists then verifies."""
    if operation == "data.acquire":
        from etalon.boundary.quarry import DataBudget
        from etalon.data.artifacts import summary
        from etalon.data.service import run_data

        return summary(run_data(params["request"], ctx.root, run_id="acquisition",
                                budget=DataBudget(**params["budget"]),
                                expected_plan_id=ctx.preflight["data_plan_id"]))
    if operation == "data.prepare":
        from etalon.data.artifacts import summary
        from etalon.data.library import prepare_library

        result = summary(prepare_library(Path(params["snapshot"]), ctx.root, run_id="library",
                         **{k: v for k, v in params.items() if k not in {"snapshot", "min_records"}}))
        return {**result, "library_path": str(Path(result["snapshot"]) / "library.csv")}
    if operation == "campaign.create":
        from etalon.active.setup import create_campaign
        from etalon.runtime.executors import register_executor

        created = create_campaign(ctx.root / "campaign.sqlite", params["spec"], params["endpoints"])
        for prepared in params["executors"]:
            register_executor(created["database"], prepared, rationale="Executor explicitly declared in frozen workflow")
        return _record_campaign_result(operation, params, created, ctx.root)
    if operation in {"campaign.import", "campaign.assays", "campaign.handoffs"}:
        store = CampaignStore(absolute(params["database"]))
        if operation == "campaign.import":
            from etalon.data.library import import_candidates

            result = import_candidates(store, Path(params["snapshot"]), candidate_ids=params.get("candidate_ids"))
        elif operation == "campaign.assays":
            from etalon.data.ingress import import_assays

            result = import_assays(store, Path(params["snapshot"]), params["review"])
        else:
            from etalon.data.ingress import attach_handoffs

            result = attach_handoffs(store, Path(params["workspace"]), params["artifact_id"],
                         rationale=params["rationale"], candidate_ids=params.get("candidate_ids"))
        # A committed import records identity facts in the campaign; do not invent new labels.
        return _record_campaign_result(operation, params,
            {"database": str(store.path), "result": result, "state": store.status()}, ctx.root)
    if operation == "screen.run":
        from etalon.screening import _prepare

        prepared, screen, plan = _prepare(params["config_path"], params["library_path"], ctx.root,
                                           allow_copyleft=params.get("allow_copyleft", False))
        if (prepared["request"]["inputs"] != ctx.preflight["screen_inputs"]
                or prepared["request"]["revision_id"] != ctx.preflight["screen_revision"]):
            raise StateError("screen inputs or compiled revision changed after preflight")
        ctx.receipt("screen_plan", prepared)
        run = screen.run(plan, run_id="screen", workers=params.get("workers", 1), devices=params.get("devices"))
        check_inputs(prepared["request"]["inputs"])
        return {"workspace": str(ctx.root), "run_id": run.run_id, "run": run.as_dict()}
    if operation == "screen.export":
        from etalon.boundary.screen import Screen

        result = Screen(params["workspace"]).export_shortlist(params["run_id"], ctx.root / "shortlist.sdf")
        return {**result, "sha256": file_hash(Path(result["path"]))}
    if operation == "active.run":
        from etalon.active.runner import ActiveCampaign
        from etalon.runtime.executors import build_executor, registered_executors

        store = CampaignStore(absolute(params["database"]))
        if digest(registered_executors(store.path)) != ctx.preflight.get("registry_hash"):
            raise StateError("registered executors changed after preflight; inspect a fresh plan")
        start = {"action_ids": [a["id"] for a in store.actions()], "balance": store.balance(),
                 "admitted": len(store.observations(admitted_only=True))}
        ctx.receipt("active_start", start)
        executor, receptor = build_executor(store.path, ctx.root / "actions", checkpoint=ctx.checkpoint,
                                             process_observer=ctx.process_observer)

        def observed(action, evaluation):
            ctx.receipt("action:" + action.id, {"action": action.as_dict(), "result": evaluation.as_dict()})

        outcome = ActiveCampaign(store, executor, receptor_path=receptor,
                                  checkpoint=ctx.checkpoint, result_observer=observed).run(max_rounds=params["max_rounds"])
        return active_result(store, start, outcome)
    raise ValueError("unregistered operation")


def _record_campaign_result(operation: str, params: dict, result: dict, root: Path) -> dict:
    """Commit the receipt and its event together; later journal additions remain legal."""
    store = CampaignStore(absolute(result["database"]))
    with store.connection(write=True) as db:
        events = store._events(db)
        body = {"schema": "etalon-runtime-campaign-receipt/1", "operation": operation,
                "parameters_sha256": digest(params), "scope": str(absolute(root).resolve()),
                "database": str(store.path), "result_sha256": digest(result),
                "event_cutoff": events[-1]["sequence"] if events else 0,
                "events_sha256": digest(events)}
        identity = digest(body)
        key = "resource:runtime:campaign-receipt:" + identity
        existing = db.execute("SELECT body FROM metadata WHERE key=?", (key,)).fetchone()
        if existing is not None and existing[0] != canonical(body):
            raise StateError("campaign receipt identity collision")
        if existing is None:
            db.execute("INSERT INTO metadata VALUES (?,?)", (key, canonical(body)))
            store._event(db, "runtime_campaign_receipt", {"receipt_id": identity, **body})
    return {**result, "receipt": {"id": identity, "scope": body["scope"]}}


class _CampaignFacts:
    """Run existing ingress validators against committed facts without mutating them."""

    def __init__(self, store: CampaignStore) -> None:
        self.store = store
        self.events = store.events()
        with store.connection() as db:
            self.metadata = {row[0]: json.loads(row[1]) for row in db.execute("SELECT key,body FROM metadata")}

    def configuration(self):
        specification, endpoints = self.store.configuration()
        configured = [event["body"] for event in self.events if event["kind"] == "configured"]
        if len(configured) != 1 or configured[0]["spec"] != specification.as_dict():
            raise StateError("campaign specification differs from its authoritative configuration event")
        expected = {value["id"]: value for value in configured[0]["endpoints"]}
        for event in self.events:
            if event["kind"] == "endpoint_registered":
                value = event["body"]["endpoint"]
                if value["id"] in expected and expected[value["id"]] != value:
                    raise StateError("campaign endpoint identity changed in its registration history")
                expected[value["id"]] = value
        if {key: value.as_dict() for key, value in endpoints.items()} != expected:
            raise StateError("campaign endpoints differ from their authoritative registration events")
        return specification, endpoints

    def candidates(self):
        original = {event["body"]["id"]: event["body"] for event in self.events
                    if event["kind"] == "candidate_added"}
        with self.store.connection() as db:
            actual = {row[0]: json.loads(row[1]) for row in db.execute("SELECT id,body FROM candidates")}
            if original != actual:
                raise StateError("candidate identity differs from its authoritative registration event")
        return self.store.candidates()

    def bind_resource(self, name, identity):
        if self.metadata.get("resource:" + name) != identity:
            raise StateError("campaign is missing or changed a required ingress resource binding")

    def add_candidates(self, candidates):
        if candidates:
            raise StateError("import receipt names candidates absent from the authoritative campaign")
        return 0

    def import_reviewed_evaluations(self, results, *, review):
        review_id = digest(review)
        if self.metadata.get("external-review:" + review_id) != review:
            raise StateError("assay review is absent or differs from the campaign record")
        if any(self.metadata.get("external-assay-target:" + result.endpoint_id) != review["target"]
               for _, result in results):
            raise StateError("assay target binding differs from the reviewed accession and construct")
        actions = {action["id"]: action for action in self.store.actions()}
        observations = {row["action_id"]: row for row in self.store.observations()}
        imported = {event["body"]["action"]["id"]: event["body"] for event in self.events
                    if event["kind"] == "observation_imported"}
        for experiment, evaluation in results:
            expected = {key: getattr(evaluation, key) for key in ("candidate_id", "endpoint_id", "value", "units")}
            source = "external-experiment:" + experiment
            binding = self.metadata.get(source, {})
            original_review = self.metadata.get("external-review:" + binding.get("review_id", ""))
            if (binding.get("identity") != expected or original_review is None
                    or digest(original_review) != binding.get("review_id")):
                raise StateError("assay experiment identity or original review does not match committed evidence")
            identifier = "import-" + digest([source, evaluation.candidate_id, evaluation.endpoint_id])
            action, observation, event = actions.get(identifier), observations.get(identifier), imported.get(identifier)
            if (action is None or observation is None or event is None or action["round_id"] != 0
                    or action["status"] != "completed" or action["cost"] != 0
                    or action["decision"].get("source_id") != source
                    or any(action.get(key) != value for key, value in event["action"].items())
                    or any(observation["result"].get(key) != value for key, value in expected.items())
                    or observation["result"] != event["result"] or observation["admitted"] != event["admitted"]
                    or not observation["admitted"] or observation["result"].get("cost") != 0
                    or observation["result"].get("status") != "ok"
                    or observation["result"].get("provenance", {}).get("review_sha256") != original_review["sha256"]):
                raise StateError("assay receipt has no matching admitted historical observation")
        return 0

    def bind_handoffs(self, rows, **_context):
        candidates = self.candidates()
        for row in rows:
            candidate = candidates.get(row["parent_id"])
            if candidate is None or candidate.smiles != row["parent_smiles"] or candidate.handoff != row:
                raise StateError("handoff receipt does not match the campaign's bound chemical state and geometry")
            binding = self.metadata.get("candidate-handoff:" + candidate.id)
            if binding is not None and not any(
                    event["kind"] == "candidate_handoff_bound"
                    and event["body"] == {"candidate_id": candidate.id, **binding} for event in self.events):
                raise StateError("handoff source binding differs from its authoritative event")
        return 0


def _verify_campaign(operation: str, params: dict, result: dict, *, workspace: Path | str | None) -> dict:
    from etalon.active.schema import CampaignSpec, Endpoint

    receipt = fields(result.get("receipt"), {"id", "scope"})
    store = CampaignStore(absolute(result["database"]), read_only=True)
    expected_scope = absolute(workspace if workspace is not None else receipt["scope"]).resolve()
    if absolute(receipt["scope"]).resolve() != expected_scope:
        raise StateError("campaign receipt belongs to another runtime node workspace")
    expected_database = expected_scope / "campaign.sqlite" if operation == "campaign.create" else absolute(params["database"]).resolve()
    if store.path != expected_database:
        raise StateError("campaign receipt belongs to a different database")
    facts = _CampaignFacts(store)
    body = facts.metadata.get("resource:runtime:campaign-receipt:" + receipt["id"])
    payload = {key: value for key, value in result.items() if key != "receipt"}
    if (not isinstance(body, dict) or digest(body) != receipt["id"] or body.get("operation") != operation
            or body.get("scope") != str(expected_scope) or body.get("database") != str(store.path)
            or body.get("parameters_sha256") != digest(params) or body.get("result_sha256") != digest(payload)):
        raise StateError("campaign receipt differs from its immutable journal record")
    prefix = [event for event in facts.events if event["sequence"] <= body["event_cutoff"]]
    if digest(prefix) != body["events_sha256"]:
        raise StateError("campaign event history changed before the receipt's checkpoint")
    if not any(event["kind"] == "runtime_campaign_receipt"
               and event["body"] == {"receipt_id": receipt["id"], **body} for event in facts.events):
        raise StateError("campaign receipt is missing its transactional journal event")
    evidence = {"database": str(store.path), "receipt_id": receipt["id"], "event_cutoff": body["event_cutoff"]}
    if operation == "campaign.create":
        from etalon.runtime.executors import registered_executors

        specification, endpoints = facts.configuration()
        expected_spec = CampaignSpec(**params["spec"]).as_dict()
        expected_endpoints = [Endpoint(**value).as_dict() for value in params["endpoints"]]
        initial = {"schema_version": 1, "spec": expected_spec,
                   "endpoints": sorted(expected_endpoints, key=lambda item: item["id"])}
        if (specification.as_dict() != expected_spec or result["spec"] != expected_spec
                or result["endpoints"] != expected_endpoints
                or any(endpoints.get(value["id"]) != Endpoint(**value) for value in expected_endpoints)
                or not any(event["kind"] == "configured" and event["body"] == initial for event in prefix)):
            raise StateError("created campaign specification or endpoints differ from their declared identity")
        registered = registered_executors(store.path)
        for prepared in params["executors"]:
            if registered.get(prepared["endpoint"]["id"]) != prepared:
                raise StateError("created campaign is missing a declared executor registration")
        evidence["endpoint_ids"] = sorted(value["id"] for value in expected_endpoints)
        return evidence
    if operation == "campaign.import":
        from etalon.data.library import import_candidates

        checked = import_candidates(facts, Path(params["snapshot"]), candidate_ids=params.get("candidate_ids"))
        expected = {key: value for key, value in checked.items() if key != "added"}
        if any(result["result"].get(key) != value for key, value in expected.items()):
            raise StateError("candidate import receipt differs from the sealed library selection")
        evidence.update(snapshot_id=checked["snapshot_id"], candidates=checked["candidates"])
    elif operation == "campaign.assays":
        from etalon.data.ingress import import_assays

        checked = import_assays(facts, Path(params["snapshot"]), params["review"])
        if any(result["result"].get(key) != value for key, value in checked.items() if key != "added"):
            raise StateError("assay import receipt differs from the reviewed sealed evidence")
        evidence.update(accepted=checked["accepted"], review_sha256=checked["review_sha256"])
    elif operation == "campaign.handoffs":
        from etalon.data.ingress import attach_handoffs

        checked = attach_handoffs(facts, Path(params["workspace"]), params["artifact_id"],
            rationale=params["rationale"], candidate_ids=params.get("candidate_ids"))
        if any(result["result"].get(key) != value for key, value in checked.items() if key != "bound"):
            raise StateError("handoff receipt differs from the verified MolCascade contract")
        evidence.update(artifact_id=checked["artifact_id"], records=checked["records"])
    return evidence


def active_result(store: CampaignStore, start: dict, outcome: dict | None = None) -> dict:
    snapshot = store.snapshot()
    actions = _active_interval(snapshot, start)
    selected = {a["id"] for a in actions}
    observations = [o for o in snapshot["observations"] if o["action_id"] in selected]
    return {"database": str(store.path), "run": outcome, "action_ids": sorted(selected),
            "active_start": start, "end_action_ids": [a["id"] for a in snapshot["actions"]],
            "observations": observations, "new_admitted": sum(o["admitted"] for o in observations),
            "cost": sum(a["cost"] for a in actions), "cost_unit": snapshot["spec"].cost_unit,
            "pending": [a for a in actions if a["status"] in {"reserved", "running"}],
            "state": store._status(snapshot)}


def _active_interval(snapshot: dict, start: dict, end: list | None = None) -> list[dict]:
    """Use append order to bound one task, including when later tasks add actions."""
    actual = [action["id"] for action in snapshot["actions"]]
    if not isinstance(start, dict):
        raise StateError("active result needs its durable starting ownership receipt")
    before = start.get("action_ids")
    after = actual if end is None else end
    for name, prefix in (("starting", before), ("ending", after)):
        if (not isinstance(prefix, list) or any(not isinstance(key, str) for key in prefix)
                or len(set(prefix)) != len(prefix) or prefix != actual[:len(prefix)]):
            raise StateError(f"active {name} actions are not a complete authoritative journal prefix")
    if len(after) < len(before):
        raise StateError("active ending actions precede the starting ownership receipt")
    prior = snapshot["actions"][:len(before)]
    prior_ids = set(before)
    balance = start.get("balance", {})
    if (not isinstance(balance, dict) or balance.get("spent") != sum(a["cost"] for a in prior)
            or balance.get("reserved") != 0
            or any(a["status"] in {"reserved", "running"} for a in prior)
            or start.get("admitted") != sum(o["admitted"] for o in snapshot["observations"]
                                            if o["action_id"] in prior_ids)):
        raise StateError("active starting balance or observations do not match its journal prefix")
    return snapshot["actions"][len(before):len(after)]


def _action_receipt_results(receipts: dict, snapshot: dict, owned: set[str]) -> dict:
    """Validate every saved outcome before replaying any of them."""
    from etalon.active.schema import Evaluation

    actions = {a["id"]: a for a in snapshot["actions"]}
    observations = {o["action_id"]: o for o in snapshot["observations"]}
    prepared = {}
    for key, receipt in receipts.items():
        prefix = next((p for p in ("action:", "settlement:") if key.startswith(p)), None)
        if prefix is None:
            continue
        action_id = key.removeprefix(prefix)
        current = actions.get(action_id)
        if action_id not in owned or current is None:
            raise StateError("outcome receipt does not belong to this active task's action interval")
        if prefix == "action:":
            expected = {k: v for k, v in current.items() if k not in {"status", "cost"}}
            if digest(receipt.get("action")) != digest(expected):
                raise StateError("result receipt does not identify the original reserved action")
        result = Evaluation.from_dict(receipt["result"])
        if ((result.candidate_id, result.endpoint_id) != (current["candidate_id"], current["endpoint_id"])
                or result.units != snapshot["endpoints"][current["endpoint_id"]].units):
            raise StateError("outcome receipt belongs to different scientific inputs or units")
        if prefix == "settlement:" and (result.status != "failed" or result.value is not None):
            raise StateError("failure settlement receipts cannot introduce a scientific value")
        if action_id in prepared and digest(prepared[action_id].as_dict()) != digest(result.as_dict()):
            raise StateError("execution and settlement receipts conflict for the same action")
        if action_id in observations:
            if digest(observations[action_id]["result"]) != digest(result.as_dict()):
                raise StateError("resolved observation differs from saved receipt")
            if current["cost"] != result.cost or current["status"] in {"reserved", "running"}:
                raise StateError("resolved action charges or status disagree with the authoritative observation")
        elif current["status"] not in {"reserved", "running"}:
            raise StateError("resolved active action is missing its authoritative observation")
        prepared[action_id] = result
    return prepared


def _active_facts(result: dict, *, database: str | None = None, active_start: dict | None = None,
                  action_receipts: dict | None = None) -> dict:
    path = absolute(result["database"]).resolve()
    if database is not None and path != absolute(database).resolve():
        raise StateError("active result belongs to a different campaign database")
    if action_receipts is not None:
        recorded_start = action_receipts.get("active_start")
        if not isinstance(recorded_start, dict):
            raise StateError("active task is missing its durable starting ownership receipt")
        if active_start is not None and digest(active_start) != digest(recorded_start):
            raise StateError("supplied starting ownership differs from the durable receipt")
        active_start = recorded_start
    start = result.get("active_start")
    if active_start is not None and digest(start) != digest(active_start):
        raise StateError("active result changed the durable starting ownership receipt")
    snapshot = CampaignStore(path, read_only=True).snapshot()
    if "end_action_ids" not in result:
        raise StateError("active result is missing its complete ending action boundary")
    actions = _active_interval(snapshot, start, result["end_action_ids"])
    selected = {a["id"] for a in actions}
    if result.get("action_ids") != sorted(selected):
        raise StateError("active result omitted or added actions from its task interval")
    if action_receipts is not None:
        receipts = _action_receipt_results(action_receipts, snapshot, selected)
        if set(receipts) != selected:
            raise StateError("active actions are missing this task's durable outcome receipts")
    observations = [o for o in snapshot["observations"] if o["action_id"] in selected]
    if {o["action_id"] for o in observations} != selected:
        raise StateError("active outcomes remain unreconciled; retain unknown cost reservations")
    return {"database": str(path), "action_ids": sorted(selected), "observations": observations,
            "cost": sum(a["cost"] for a in actions), "cost_unit": snapshot["spec"].cost_unit,
            "pending": [a for a in actions if a["status"] in {"reserved", "running"}],
            "new_admitted": sum(o["admitted"] for o in observations)}


def verify(operation: str, params: dict, result: dict, *, active_start: dict | None = None,
           action_receipts: dict | None = None, workspace: str | Path | None = None) -> dict:
    if not isinstance(result, dict):
        raise ValueError("operation did not return a structured result")
    evidence = {"operation": operation, "result_sha256": digest(result)}
    if operation in {"data.acquire", "data.prepare"}:
        snapshot = read_snapshot(Path(result["snapshot"]))
        status = snapshot["result"].get("status")
        allowed = {"query": {"complete"}, "import_catalog": {"complete"}, "search_catalog": {"complete"},
                   "collect": {"collected_requires_review"}, "sourcing": {"completed_with_declared_scope"},
                   "download": {"downloaded"}, "library": {"prepared"}}
        accepted = status in allowed.get(snapshot["kind"], set())
        if snapshot["kind"] == "bundle":
            accepted = (status is None and "payload/manifest.json" in snapshot["files"]
                        and isinstance(snapshot["result"].get("candidate_count"), int)
                        and (params.get("allow_partial", False) or not (
                            snapshot["result"].get("structure_problems") or snapshot["result"].get("collection_incomplete_steps"))))
        if not accepted and not (status == "partial" and params.get("allow_partial", False)):
            raise StateError("sealed data did not satisfy the requested coverage; inspect retained partial evidence")
        count = snapshot["result"].get("candidates", snapshot["result"].get("returned",
                    snapshot["result"].get("parsed_records", snapshot["result"].get("candidate_count", 0))))
        if count < params.get("min_records", 1 if operation == "data.prepare" else 0):
            raise StateError("data result does not meet the fixed minimum record count")
        evidence.update(snapshot_id=snapshot["snapshot_id"], coverage=status, records=count)
    elif operation == "screen.run":
        from etalon.boundary.screen import Screen

        run = result["run"]
        if run["status"] != "SUCCEEDED" or run["failed_stages"] or not run["stages"]:
            raise StateError("screening did not finish successfully; Python return is not scientific success")
        screen = Screen(result["workspace"])
        from molcascade.artifacts.store import LocalArtifactStore

        artifacts = LocalArtifactStore(screen.workspace)
        ids = [s["artifact_id"] for s in run["stages"] if s["artifact_id"] is not None and s["error"] is None]
        if len(ids) != len(run["stages"]):
            raise StateError("screen has missing or uncommitted stages")
        for key in ids:
            artifacts.artifact_directory(key, verify=True)
        evidence["artifact_ids"] = ids
    elif operation == "screen.export":
        if file_hash(Path(result["path"])) != result["sha256"]:
            raise StateError("exported shortlist changed")
        evidence["sha256"] = result["sha256"]
    elif operation == "active.run":
        actual = _active_facts(result, database=params["database"], active_start=active_start,
                               action_receipts=action_receipts)
        if digest(actual["observations"]) != digest(result["observations"]):
            raise StateError("active result does not match the authoritative observations")
        if result["pending"] or actual["pending"]:
            raise StateError("active outcomes remain unreconciled")
        if (type(result.get("cost")) not in {int, float} or result["cost"] != actual["cost"]
                or result.get("cost_unit") != actual["cost_unit"]
                or type(result.get("new_admitted")) is not int
                or result["new_admitted"] != actual["new_admitted"]):
            raise StateError("active result charges, units or admission count differ from the authoritative journal")
        admitted = actual["new_admitted"]
        if admitted < params.get("min_new_admitted", 1):
            raise StateError("active task did not produce the required new admitted observations")
        evidence.update(admitted=admitted, action_ids=actual["action_ids"], cost=actual["cost"],
                        cost_unit=actual["cost_unit"], active_start_sha256=digest(result["active_start"]),
                        admission="CampaignStore scientific rulings; not model self-evaluation")
    elif operation in {"campaign.create", "campaign.import", "campaign.assays", "campaign.handoffs"}:
        evidence.update(_verify_campaign(operation, params, result, workspace=workspace))
    else:
        raise ValueError("unregistered operation cannot be verified")
    return evidence


def accounting(operation: str, result: dict, resources: dict, *, database: str | None = None,
               active_start: dict | None = None, action_receipts: dict | None = None) -> tuple[dict, dict]:
    spent = dict(resources)
    basis = dict.fromkeys(resources, "declared execution quote; not measured")
    if operation == "data.acquire":
        usage = read_snapshot(Path(result["snapshot"]))["usage"]
        for unit, key in (("http_requests", "requests"), ("http_bytes", "response_bytes")):
            if unit in spent:
                spent[unit], basis[unit] = usage[key], "measured by MolQuarry request/stream meter"
    if operation == "active.run":
        actual = _active_facts(result, database=database, active_start=active_start, action_receipts=action_receipts)
        if actual["pending"]:
            raise StateError("active outcomes remain unreconciled; retain unknown cost reservations")
        unit = "campaign:" + actual["cost_unit"]
        if unit not in spent:
            raise StateError("active costs have no reservation in the campaign's authoritative cost unit")
        spent[unit] = actual["cost"]
        basis[unit] = "sum of action charges in CampaignStore; executor provenance distinguishes quotes from measurements"
    return spent, basis


def recover(operation: str, params: dict, ctx: Any) -> dict | None:
    """Recover existing facts, never execute a missing scientific action."""
    receipts = ctx.receipts()
    if "completed" in receipts:
        result = receipts["completed"]["result"]
        if operation == "active.run":
            if "active_start" not in receipts:
                raise StateError("active completion has no durable starting ownership receipt")
            _active_facts(result, database=params["database"], active_start=receipts["active_start"],
                          action_receipts=receipts)
        return result
    if operation in {"data.acquire", "data.prepare"}:
        path = ctx.root / "data" / ("acquisition" if operation == "data.acquire" else "library")
        if (path / "snapshot.json").is_file():
            from etalon.data.artifacts import summary

            result = summary(read_snapshot(path))
            if operation == "data.prepare":
                result["library_path"] = str(path / "library.csv")
            return result
    if operation == "active.run" and "active_start" in receipts:
        store = CampaignStore(absolute(params["database"]))
        snapshot = store.snapshot()
        owned = {a["id"] for a in _active_interval(snapshot, receipts["active_start"])}
        observations = {o["action_id"] for o in snapshot["observations"]}
        prepared = _action_receipt_results(receipts, snapshot, owned)
        for action_id, result in prepared.items():
            if action_id not in observations:
                store.resolve(action_id, result)
        # Another direct runner can share this campaign without using the runtime
        # lock. Its results cannot establish this task's ownership or success.
        if set(prepared) != owned or store.status()["pending"]:
            return None
        store.recover_idle_rounds(reason="Runtime owner stopped; durable action receipts reconciled without executing tools")
        return active_result(store, receipts["active_start"])
    return None
