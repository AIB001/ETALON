"""Strict JSON workflow contracts; references carry data, never executable code."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from etalon.active.schema import canonical, digest, finite

SCHEMA = "etalon-workflow/1"


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("identifier must contain 1–80 letters, digits, underscores or hyphens")
    return value


def fields(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise ValueError(f"expected required fields {sorted(required)} and optional fields {sorted(optional)}")
    canonical(value)
    return value


def bounded_int(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


def text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def absolute(value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not Path(value).is_absolute():
        raise ValueError("runtime paths must be absolute")
    return Path(value).absolute()


def amounts(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("resource amounts must map explicit units to finite nonnegative amounts")
    for unit, amount in value.items():
        text(unit, "resource unit")
        finite(amount, unit, minimum=0)
    return {unit: float(amount) for unit, amount in value.items()}


def references(value: Any) -> set[str]:
    if isinstance(value, dict):
        if "$ref" in value:
            fields(value, {"$ref"})
            parts = text(value["$ref"], "reference").split(".")
            identifier(parts[0])
            if len(parts) < 2 or any(not p for p in parts):
                raise ValueError("references use node_id.output_field (and optional nested fields)")
            return {parts[0]}
        return set().union(*(references(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(references(v) for v in value))
    return set()


def resolve(value: Any, outputs: Mapping[str, dict]) -> Any:
    if isinstance(value, dict):
        if "$ref" in value:
            parts = value["$ref"].split(".")
            result = outputs[parts[0]]
            for part in parts[1:]:
                if not isinstance(result, dict) or part not in result:
                    raise ValueError(f"unavailable output reference {value['$ref']}")
                result = result[part]
            return json.loads(canonical(result))
        return {k: resolve(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, outputs) for v in value]
    return value


def workflow(value: dict) -> dict:
    from etalon.runtime.operations import describe, validate_arguments

    fields(value, {"schema", "objective", "nodes", "limits"}, {"controller", "max_seconds"})
    if value["schema"] != SCHEMA:
        raise ValueError(f"workflow schema must be {SCHEMA}")
    text(value["objective"], "objective")
    limits = amounts(value["limits"])
    max_seconds = value.get("max_seconds", 3600)
    finite(max_seconds, "max_seconds", minimum=1)
    if max_seconds > 30 * 86400:
        raise ValueError("workflow wall-clock limit exceeds 30 days")
    if not isinstance(value["nodes"], list) or not 1 <= len(value["nodes"]) <= 100:
        raise ValueError("a workflow needs 1–100 explicit nodes")
    nodes, known = [], set()
    for raw in value["nodes"]:
        fields(raw, {"id", "operation", "arguments"}, {"depends_on", "resources"})
        key = identifier(raw["id"])
        if key in known or key in {"finish", "pause"} or raw["operation"] not in describe():
            raise ValueError("duplicate node or unregistered operation")
        validate_arguments(raw["operation"], raw["arguments"], deferred=True)
        deps = raw.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise ValueError("depends_on must be a list of node identifiers")
        deps = sorted(set(deps) | references(raw["arguments"]))
        resources = amounts(raw.get("resources", {}))
        if any(unit not in limits or amount > limits[unit] for unit, amount in resources.items()):
            raise ValueError("each resource reservation requires a sufficient workflow limit in the same unit")
        nodes.append({**raw, "depends_on": deps, "resources": resources})
        known.add(key)
    available = set()
    while len(available) < len(nodes):
        ready = {n["id"] for n in nodes if set(n["depends_on"]) <= available} - available
        if not ready:
            raise ValueError("workflow contains a cycle or a dependency on an unknown node")
        available |= ready
    controller = value.get("controller", {"mode": "ordered"})
    fields(controller, {"mode"}, {"max_calls"})
    if controller["mode"] not in {"ordered", "advisor", "external"}:
        raise ValueError("controller mode must be ordered, advisor or external")
    controller = {**controller, "max_calls": bounded_int(controller.get("max_calls", 100), "max_calls", 1, 1000)}
    return {**value, "nodes": nodes, "limits": limits, "max_seconds": float(max_seconds), "controller": controller}


def plan_id(spec: dict, inputs: dict) -> str:
    return digest({"spec": spec, "inputs": inputs})
