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
    else:
        raise ValueError("executor kind must be molcascade or prism")
    body = {"schema": SCHEMA, "kind": kind, "configuration": normalized,
            "endpoint": contract.as_dict(), "inputs": inputs}
    return {**body, "executor_id": digest(body)}


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
        _, endpoints = store._configuration(db)
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
                   process_observer: Callable[[int], None] | None = None) -> tuple[Callable, Path | None]:
    """Reconstruct only registered dispatchers, preserving all endpoint admission guards."""
    root = absolute(workspace)
    for callback in (checkpoint, process_observer):
        if callback is not None and not callable(callback):
            raise ValueError("executor callbacks must be callable")
    store = _store(database, read_only=True)
    _, endpoints = store.configuration()
    registered = registered_executors(database)
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
        try:
            _verify(record)
        except (OSError, ValueError) as error:
            return Evaluation(candidate.id, endpoint.id, None, endpoint.units, 0.0, status="blocked",
                provenance={"phase": "executor_preflight", "executor_id": record["executor_id"],
                            "error": str(error), "cost_basis": "not dispatched"})
        if record["kind"] == "molcascade":
            result = cascade(action, candidate, endpoint, grant)
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
