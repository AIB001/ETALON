"""Persist typed scientific dispatchers; registration never runs an experiment."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout, _file_hash
from etalon.active.schema import Endpoint, Evaluation, canonical, digest, finite
from etalon.active.store import CampaignStore, StateError
from etalon.runtime.schema import absolute, fields, text

SCHEMA = "etalon-executor/1"
_PREFIX = "runtime:executor:"


def _endpoint(value: dict, protocol: str, fixed: dict[str, Any]) -> Endpoint:
    if not isinstance(value, dict):
        raise ValueError("endpoint must be a mapping")
    supplied = dict(value)
    for key, expected in {"protocol": protocol, **fixed}.items():
        if key in supplied and (type(supplied[key]) is not type(expected) or supplied[key] != expected):
            raise ValueError(f"executor requires endpoint {key}={expected!r}")
        supplied[key] = expected
    return Endpoint(**supplied)


def _recipe(configuration: dict) -> CascadeRecipe:
    return CascadeRecipe.freeze(configuration["cascade"], Readout(**configuration["readout"]),
                                files=tuple(configuration["files"]))


def prepare_executor(kind: str, configuration: dict, endpoint: dict) -> dict:
    """Validate a registered implementation and freeze its scientific inputs, without dispatch."""
    if kind == "molcascade":
        fields(configuration, {"cascade", "readout"}, {"files"})
        fields(configuration["readout"], {"stage_id", "contract_id", "value_column"}, {"filters"})
        for key in ("stage_id", "contract_id", "value_column"):
            text(configuration["readout"][key], key)
        if not isinstance(configuration["readout"].get("filters", {}), dict):
            raise ValueError("readout filters must be a mapping")
        files = configuration.get("files", [])
        if not isinstance(files, list):
            raise ValueError("executor files must be a list of absolute paths")
        paths = [str(absolute(path)) for path in files]
        recipe = CascadeRecipe.freeze(configuration["cascade"], Readout(**configuration["readout"]),
                                      files=tuple(paths))
        normalized = {"cascade": json.loads(recipe.configuration),
                      "readout": json.loads(recipe.readout_json),
                      "files": [path for path, _ in recipe.input_files]}
        inputs = dict(recipe.input_files)
        contract = _endpoint(endpoint, recipe.protocol_id,
                             {"requires_handoff": False, "queryable": True, "max_replicates": 1})
    elif kind == "prism":
        from etalon.boundary.infra import load

        fields(configuration, {"receptor_path", "python", "production_ns"}, {"timeout_per_molecule"})
        receptor, interpreter = absolute(configuration["receptor_path"]), absolute(configuration["python"])
        duration, timeout = configuration["production_ns"], configuration.get("timeout_per_molecule", 86_400)
        for name, value in (("production_ns", duration), ("timeout_per_molecule", timeout)):
            finite(value, name, minimum=0)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        normalized = {"receptor_path": str(receptor), "python": str(interpreter),
                      "production_ns": float(duration), "timeout_per_molecule": float(timeout)}
        inputs = {str(path): _file_hash(path) for path in sorted({receptor, interpreter})}
        infra = load("prism")
        protocol = "prism-mmpbsa/1:" + digest({"configuration": normalized, "inputs": inputs,
            "infrastructure": {"source_commit": infra.source_commit, "tree_sha256": infra.tree_sha256,
                               "version": infra.version}})
        contract = _endpoint(endpoint, protocol, {"quantity": "mmpbsa", "units": "kcal/mol",
            "direction": "minimize", "requires_handoff": True, "queryable": True, "max_replicates": 1})
    elif kind == "prism_bridge_v1":
        normalized, inputs = _bridge_configuration(configuration)
        template = normalized["request_template"]
        contract = _endpoint({"max_replicates": len(normalized["seeds"]), **endpoint}, template["protocol_id"], {
            "id": template["endpoint_id"], "quantity": "mean_residue_contacts", "units": "count",
            "direction": "maximize", "requires_handoff": True, "queryable": True})
    else:
        raise ValueError("executor kind must be molcascade, prism or prism_bridge_v1")
    body = {"schema": SCHEMA, "kind": kind, "configuration": normalized,
            "endpoint": contract.as_dict(), "inputs": inputs}
    return {**body, "executor_id": digest(body)}


def _bridge_configuration(configuration: dict) -> tuple[dict, dict]:
    from pathlib import PurePosixPath
    from etalon.boundary.prism_v1 import CLITransport, validate_request

    fields(configuration, {"request_template", "transport", "job_root", "receptor_path", "seeds"},
           {"poll_seconds", "wait_seconds"})
    fields(configuration["transport"], {"python", "cwd", "request_root"}, {"host", "timeout"})
    transport = CLITransport(**configuration["transport"])
    validate_request(configuration["request_template"])
    seeds = configuration["seeds"]
    if (not isinstance(seeds, list) or not seeds or len(set(seeds)) != len(seeds)
            or any(type(s) is not int or not 0 <= s <= 2147483647 for s in seeds)):
        raise ValueError("bridge seeds must be distinct supported integers")
    normalized = json.loads(canonical(configuration))
    normalized["receptor_path"] = str(absolute(configuration["receptor_path"]))
    for key, default in (("poll_seconds", 2), ("wait_seconds", 3600)):
        value = configuration.get(key, default)
        finite(value, key, minimum=.001)
        normalized[key] = float(value)
    if transport.host:
        if not PurePosixPath(configuration["job_root"]).is_absolute():
            raise ValueError("SSH job_root must be an absolute POSIX path")
    else:
        absolute(configuration["job_root"])
    receptor_hash = _file_hash(Path(normalized["receptor_path"]))
    if receptor_hash != configuration["request_template"]["inputs"]["protein"]["sha256"]:
        raise ValueError("local authorization receptor differs from target-host protein")
    for artifact in configuration["request_template"]["inputs"].values():
        transport.verify(artifact)
    return normalized, {"receptor_sha256": receptor_hash, "producer": transport.fingerprint()}


def _bridge(store: CampaignStore, configuration: dict):
    from etalon.active.prism import PrismV1Executor
    from etalon.boundary.prism_v1 import CLITransport

    return PrismV1Executor(store, CLITransport(**configuration["transport"]),
                           **{k: v for k, v in configuration.items() if k != "transport"})


def _identity(prepared: dict) -> None:
    fields(prepared, {"schema", "kind", "configuration", "endpoint", "inputs", "executor_id"})
    if prepared["schema"] != SCHEMA or prepared["executor_id"] != digest(
            {key: value for key, value in prepared.items() if key != "executor_id"}):
        raise ValueError("executor record differs from its prepared identity")


def _verify(prepared: dict) -> None:
    _identity(prepared)
    fresh = prepare_executor(prepared["kind"], prepared["configuration"], prepared["endpoint"])
    if canonical(fresh) != canonical(prepared):
        raise ValueError("executor inputs, infrastructure or configuration changed since preparation")


def _store(database: str | Path, *, read_only: bool = False) -> CampaignStore:
    path = absolute(database)
    if not path.is_file():
        raise FileNotFoundError(f"configure a campaign before registering executors: {path}")
    return CampaignStore(path, read_only=read_only)


class _TransactionStore:
    """Let ProtocolRegistry share the surrounding registration transaction."""

    def __init__(self, db: Any) -> None:
        self.db = db

    @contextmanager
    def connection(self, *, write: bool = False):
        if not write:
            raise StateError("executor registration expects the registry's write transaction")
        yield self.db

    _event = staticmethod(CampaignStore._event)


def register_executor(database: str | Path, prepared: dict, rationale: str) -> dict:
    """Bind an immutable dispatcher to an existing endpoint in one journal transaction."""
    from etalon.active.protocols import ProtocolRegistry

    text(rationale, "executor registration rationale")
    _verify(prepared)
    record = json.loads(canonical(prepared))
    store = _store(database)
    endpoint = Endpoint(**record["endpoint"])
    with store.connection(write=True) as db:
        spec, endpoints = store._configuration(db)
        if endpoint.id not in endpoints or endpoints[endpoint.id] != endpoint:
            raise ValueError("executor endpoint must exactly match its registered campaign endpoint")
        key = _PREFIX + endpoint.id
        previous = db.execute("SELECT body FROM metadata WHERE key=?", (key,)).fetchone()
        added = previous is None
        if previous is not None:
            if previous[0] != canonical(record):
                raise StateError("registered executor is immutable; use a new endpoint id")
        else:
            if (db.execute("SELECT 1 FROM actions WHERE status IN ('reserved','running')").fetchone()
                    or db.execute("SELECT 1 FROM rounds WHERE status='running'").fetchone()):
                raise StateError("resolve pending actions and rounds before registering an executor")
            if record["kind"] == "prism_bridge_v1":
                if spec.cost_unit != "wall_second":
                    raise ValueError("prism_bridge_v1 requires campaign cost_unit=wall_second")
                for existing in db.execute("SELECT body FROM metadata WHERE key LIKE ?", (_PREFIX + "%",)):
                    if json.loads(existing[0])["kind"] == "prism_bridge_v1":
                        raise StateError("bounded bridge supports one fixed template per campaign")
            if record["kind"] == "molcascade":
                ProtocolRegistry(_TransactionStore(db)).bind_seed(endpoint.id, _recipe(record["configuration"]),
                                                                 rationale=rationale)
            db.execute("INSERT INTO metadata VALUES (?,?)", (key, canonical(record)))
            store._event(db, "runtime_executor_registered", {"endpoint_id": endpoint.id,
                "executor_id": record["executor_id"], "kind": record["kind"], "rationale": rationale})
    return {"database": str(store.path), "registered": added, "endpoint_id": endpoint.id,
            "executor_id": record["executor_id"], "executor": record}


def registered_executors(database: str | Path) -> dict:
    """Read registered records by endpoint ID, without creating a journal or running tools."""
    store = _store(database, read_only=True)
    with store.connection() as db:
        rows = db.execute("SELECT key,body FROM metadata WHERE key LIKE ? ORDER BY key", (_PREFIX + "%",)).fetchall()
    result = {}
    for row in rows:
        record = json.loads(row["body"])
        _identity(record)
        identifier = row["key"][len(_PREFIX):]
        if record["endpoint"]["id"] != identifier:
            raise StateError("executor registry key and endpoint identity disagree")
        result[identifier] = record
    return result


def build_executor(database: str | Path, workspace: str | Path,
                   checkpoint: Callable[[], None] | None = None,
                   process_observer: Callable[[int], None] | None = None,
                   endpoint_ids: tuple[str, ...] | None = None) -> tuple[Callable, Path | None]:
    """Reconstruct only registered dispatchers, preserving all endpoint admission guards."""
    root = absolute(workspace)
    for callback in (checkpoint, process_observer):
        if callback is not None and not callable(callback):
            raise ValueError("executor callbacks must be callable")
    store = _store(database, read_only=True)
    _, endpoints = store.configuration()
    registered = registered_executors(database)
    if endpoint_ids is not None:
        if not endpoint_ids or any(key not in endpoints for key in endpoint_ids):
            raise ValueError("selected executor endpoints must already exist")
        endpoints = {key: endpoints[key] for key in endpoint_ids}
        registered = {key: value for key, value in registered.items() if key in endpoints}
    missing = {key for key, endpoint in endpoints.items() if endpoint.queryable} - registered.keys()
    if missing:
        raise ValueError(f"queryable endpoints have no registered executor: {sorted(missing)}")
    recipes, receptors = {}, set()
    for key, record in registered.items():
        if key not in endpoints or record["endpoint"] != endpoints[key].as_dict():
            raise StateError("registered executor differs from the campaign endpoint")
        _verify(record)
        if record["kind"] == "molcascade":
            recipes[key] = _recipe(record["configuration"])
        else:
            receptors.add(record["configuration"]["receptor_path"])
    if len(receptors) > 1:
        raise ValueError("one active campaign requires a consistent PRISM receptor path")
    cascade = CascadeExecutor(root, recipes)
    receptor_path = Path(next(iter(receptors))) if receptors else None

    def dispatch(action, candidate, endpoint, grant):
        if checkpoint is not None:
            checkpoint()
        record = registered.get(endpoint.id)
        if (record is None or record["endpoint"] != endpoint.as_dict()
                or action.endpoint_id != endpoint.id or action.candidate_id != candidate.id):
            raise ValueError("action is not bound to this registered executor")
        if record["kind"] == "prism_bridge_v1":
            for observation in store.observations():
                if observation["action_id"] == action.id:
                    return Evaluation.from_dict(observation["result"])
        try:
            _verify(record)
        except Exception as error:
            if record["kind"] != "prism_bridge_v1" and not isinstance(error, (OSError, ValueError)):
                raise
            if record["kind"] == "prism_bridge_v1":
                from etalon.active.runner import ExecutionPending

                with store.connection() as db:
                    previous = db.execute("SELECT 1 FROM metadata WHERE key=?",
                                          ("prism-v1:request:" + action.id,)).fetchone()
                if previous:
                    raise ExecutionPending("existing bridge action cannot be reclassified after environment drift") from error
            return Evaluation(candidate.id, endpoint.id, None, endpoint.units, 0.0, status="blocked",
                provenance={"phase": "executor_preflight", "executor_id": record["executor_id"],
                            "error": str(error), "cost_basis": "not dispatched"})
        if record["kind"] == "molcascade":
            result = cascade(action, candidate, endpoint, grant)
        elif record["kind"] == "prism_bridge_v1":
            from etalon.active.runner import ExecutionPending

            writable = CampaignStore(store.path)
            observations = {o["action_id"]: o for o in writable.observations()}
            if action.id in observations:
                return Evaluation.from_dict(observations[action.id]["result"])
            template = record["configuration"]["request_template"]
            if (candidate.id != template["candidate_id"] or candidate.smiles != "c1ccccc1"
                    or action.replicate >= len(record["configuration"]["seeds"])):
                return Evaluation(candidate.id, endpoint.id, None, endpoint.units, 0, status="blocked",
                                  provenance={"error": "UNSUPPORTED: fixed bounded benzene template",
                                              "executor_id": record["executor_id"]})
            try:
                bridge = _bridge(writable, record["configuration"])
                # Record the original authorization for read-only result reconciliation.
                with writable.connection(write=True) as db:
                    db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)",
                               ("runtime:bridge-authorization:" + action.id, canonical(grant.as_dict() if grant else None)))
                result = bridge(action, candidate, endpoint, grant)
            except ExecutionPending:
                raise
        else:
            from etalon.active.adapters import StageExecutor
            from etalon.boundary.simulate import Simulate, discover
            from etalon.campaign.expensive import PrismStage

            config = record["configuration"]

            def factory(authorized_action):
                environment = discover(config["python"])
                simulation = Simulate(root / authorized_action.id, environment,
                                      process_observer=process_observer)
                return PrismStage(simulation, Path(config["receptor_path"]),
                    production_ns=config["production_ns"], timeout_per_molecule=config["timeout_per_molecule"])

            result = StageExecutor({endpoint.id: factory})(action, candidate, endpoint, grant)
        return result

    return dispatch, receptor_path


def recover_bridge_action(database: str | Path, action) -> Evaluation | None:
    """Reconcile an acknowledged bridge action without spending or resubmitting anything."""
    from dataclasses import replace
    from etalon.faults.preflight import check_record

    store = _store(database)
    record = registered_executors(database).get(action.endpoint_id)
    if not record or record["kind"] != "prism_bridge_v1":
        return None
    _verify(record)
    with store.connection() as db:
        authorization = db.execute("SELECT body FROM metadata WHERE key=?",
                                   ("runtime:bridge-authorization:" + action.id,)).fetchone()
    if authorization is None:
        return None
    result = _bridge(store, record["configuration"]).read_existing(action)
    if result is None:
        return None
    candidate = store.candidates()[action.candidate_id]
    checks = check_record(candidate.handoff, receptor_path=Path(record["configuration"]["receptor_path"]))
    return replace(result, checks=checks + result.checks, provenance={**result.provenance,
                   "authorization": json.loads(authorization[0]), "endpoint_protocol": record["endpoint"]["protocol"]})
