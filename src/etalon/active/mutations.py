"""Finite, exact-allowlisted changes to a frozen MolCascade recipe.

This is a proposal mechanism, not execution or promotion. It cannot change the measured
observable, input resources or molecular registration. Contract/dependency compilation and
trial-based promotion belong to the protocol registry, not to this syntactic mutation layer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etalon.active.cascade import (
    CascadeRecipe,
    Readout,
    _absolute_input_path,
    _configuration_input_paths,
    _file_hash,
)
from etalon.active.schema import canonical, digest
from etalon.boundary.infra import load

_FIELDS = {
    "set_setting": {"op", "criterion_id", "path", "expected", "value"},
    "reorder_criteria": {"op", "tier_id", "order"},
    "insert_criterion": {"op", "tier_id", "before", "criterion"},
}
_RESOURCE_OR_CODE = {
    "path", "paths", "file", "files", "filename", "filenames", "filepath",
    "dir", "directory", "directories", "folder", "command", "commands", "cmd",
    "shell", "script", "scripts", "code", "executable", "exec", "binary",
    "binaries", "module", "import", "entrypoint", "url", "uri", "weights",
    "checkpoint", "backend", "plugin",
}


def _json_copy(value: Any) -> Any:
    """Reject non-JSON mapping keys rather than silently stringifying identities."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("mutation JSON object keys must be strings")
        for child in value.values():
            _json_copy(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _json_copy(child)
    try:
        return json.loads(canonical(value))
    except (TypeError, ValueError) as error:
        raise ValueError("mutations must contain finite JSON values") from error


def _forbidden_key(key: str) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
    return bool(set(normalized.split("_")) & _RESOURCE_OR_CODE) or normalized == "schema_version"


def _check_new_values(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _forbidden_key(key) and key != "schema_version" and child not in (None, "", [], {}):
                raise ValueError(f"resource/code setting {key!r} is outside this design space")
            _check_new_values(child)
    elif isinstance(value, list):
        for child in value:
            _check_new_values(child)
    elif isinstance(value, str) and (
            value.startswith(("/", "~", "\\\\", "./", "../", ".\\", "..\\"))
            or re.match(r"^[A-Za-z]:[\\/]", value)
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value)):
        raise ValueError("new resource paths/URIs are outside this design space")


def _pointer(path: Any) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        raise ValueError("setting path must be a non-root JSON pointer relative to settings")
    if re.search(r"~(?![01])", path):
        raise ValueError("setting path contains invalid JSON pointer escaping")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
    if any(not part or _forbidden_key(part) for part in parts):
        raise ValueError("resource/code/schema fields cannot be changed by set_setting")
    return parts


def _edit(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("each allowed edit must be a JSON object")
    edit = _json_copy(value)
    op = edit.get("op")
    if not isinstance(op, str) or op not in _FIELDS:
        raise ValueError("unknown mutation operation")
    if set(edit) - {"allowed_failures"} != _FIELDS[op]:
        raise ValueError(f"{op} requires exactly {sorted(_FIELDS[op])}, plus optional allowed_failures")
    failures = edit.get("allowed_failures", [])
    if (not isinstance(failures, list)
            or any(not isinstance(code, str) or not code.strip() for code in failures)
            or len(set(failures)) != len(failures)):
        raise ValueError("allowed_failures must be a list of unique nonempty strings")
    identifier = "criterion_id" if op == "set_setting" else "tier_id"
    if not isinstance(edit[identifier], str) or not edit[identifier].strip():
        raise ValueError(f"{identifier} must be a nonempty string")
    if op == "set_setting":
        _pointer(edit["path"])
        _check_new_values(edit["value"])
    elif op == "reorder_criteria":
        order = edit["order"]
        if (not isinstance(order, list) or not order
                or any(not isinstance(item, str) or not item for item in order)
                or len(set(order)) != len(order)):
            raise ValueError("order must contain unique nonempty criterion ids")
    else:
        if edit["before"] is not None and (not isinstance(edit["before"], str) or not edit["before"]):
            raise ValueError("before must be an existing criterion id or null")
        if not isinstance(edit["criterion"], dict):
            raise ValueError("insert_criterion requires a complete criterion object")
        _check_new_values(edit["criterion"].get("settings", {}))
    return edit


def _operation_identity(edit: dict[str, Any]) -> str:
    return canonical({key: value for key, value in edit.items() if key != "allowed_failures"})


@dataclass(frozen=True, init=False)
class DesignSpace:
    """An immutable finite operator set; returned dictionaries are detached copies."""

    _allowed_edits_json: str
    max_edits: int

    def __init__(self, allowed_edits: tuple[dict[str, Any], ...], max_edits: int = 3) -> None:
        if isinstance(max_edits, bool) or not isinstance(max_edits, int) or max_edits < 1:
            raise ValueError("max_edits must be a positive integer")
        if not isinstance(allowed_edits, (tuple, list)):
            raise ValueError("allowed_edits must be a sequence of edit objects")
        edits = [_edit(edit) for edit in allowed_edits]
        identities = [_operation_identity(edit) for edit in edits]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate operations in the design space")
        # Enumeration order is not part of a finite set's meaning.
        edits.sort(key=canonical)
        object.__setattr__(self, "_allowed_edits_json", canonical(edits))
        object.__setattr__(self, "max_edits", max_edits)

    @property
    def allowed_edits(self) -> tuple[dict[str, Any], ...]:
        return tuple(json.loads(self._allowed_edits_json))

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "allowed_edits": json.loads(self._allowed_edits_json),
                "max_edits": self.max_edits}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DesignSpace:
        if (not isinstance(value, dict) or set(value) != {"schema_version", "allowed_edits", "max_edits"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("unsupported or malformed design space")
        return cls(value["allowed_edits"], value["max_edits"])

    @property
    def digest(self) -> str:
        return digest(self.as_dict())

    @property
    def fingerprint(self) -> str:
        return "etalon-design-space/1:" + self.digest


def _verify_base(base: CascadeRecipe) -> None:
    if load("molcascade").source_commit != base.infrastructure_commit:
        raise ValueError("base recipe infrastructure changed; freeze and review a new base")
    pinned = {str(_absolute_input_path(name)) for name, _ in base.input_files}
    required = {str(path) for path in _configuration_input_paths(json.loads(base.configuration))}
    if required - pinned:
        raise ValueError("base recipe configuration contains unpinned resource paths")
    for name, expected in base.input_files:
        try:
            actual = _file_hash(Path(name))
        except OSError as error:
            raise ValueError(f"base recipe resource is missing or unreadable: {name}") from error
        if actual != expected:
            raise ValueError(f"base recipe resource changed: {name}")


def _tier(config: dict[str, Any], identifier: str) -> dict[str, Any]:
    matches = [tier for tier in config["tiers"] if tier["id"] == identifier]
    if len(matches) != 1:
        raise ValueError(f"tier {identifier!r} does not identify exactly one tier")
    if not matches[0].get("enabled", True):
        raise ValueError("editing a disabled tier would not change the executed protocol")
    return matches[0]


def _setting(criterion: dict[str, Any], edit: dict[str, Any]) -> None:
    parts = _pointer(edit["path"])
    parent: Any = criterion["settings"]

    def index(node: Any, part: str) -> str | int:
        if isinstance(node, list):
            if not part.isascii() or not part.isdigit() or str(int(part)) != part:
                raise ValueError("list indices must be canonical nonnegative integers")
            return int(part)
        if not isinstance(node, dict):
            raise ValueError("setting path traverses a scalar")
        return part

    try:
        for part in parts[:-1]:
            parent = parent[index(parent, part)]
        leaf = index(parent, parts[-1])
        previous = parent[leaf]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("setting path must identify an existing field") from error
    if canonical(previous) != canonical(edit["expected"]):
        raise ValueError("setting does not match its declared expected value")
    if canonical(previous) == canonical(edit["value"]):
        raise ValueError("setting edit makes no effective change")
    # A pointer to an ancestor must not bypass the forbidden-field rule by deleting
    # a resource, executable or schema child instead of directly editing that child.
    def protected(value: Any, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], str]:
        found = {}
        if isinstance(value, dict):
            for key, child in value.items():
                if _forbidden_key(key):
                    found[(*path, key)] = canonical(child)
                found.update(protected(child, (*path, key)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                found.update(protected(child, (*path, str(index))))
        return found

    if protected(previous) != protected(edit["value"]):
        raise ValueError("setting replacement cannot change or remove protected resource/code/schema fields")
    _check_new_values(previous)
    parent[leaf] = _json_copy(edit["value"])


def mutate_recipe(base: CascadeRecipe, edits: Sequence[dict[str, Any]],
                  space: DesignSpace) -> CascadeRecipe:
    """Apply only explicitly enumerated changes; never run or register a protocol.

    ``allowed_failures`` is immutable operator metadata consumed by the protocol registry.
    This function does not infer a failure cause or claim that a mutation repairs one.
    """
    if not isinstance(space, DesignSpace):
        raise TypeError("space must be a DesignSpace")
    if not isinstance(edits, (tuple, list)) or not 1 <= len(edits) <= space.max_edits:
        raise ValueError("supply at least one edit and no more than max_edits")
    selected = [_edit(edit) for edit in edits]
    allowed = {canonical(edit) for edit in space.allowed_edits}
    if any(canonical(edit) not in allowed for edit in selected):
        raise ValueError("edit is not an exact member of the frozen design space")
    if len({_operation_identity(edit) for edit in selected}) != len(selected):
        raise ValueError("duplicate mutation operations")
    _verify_base(base)
    config = json.loads(base.configuration)
    for edit in selected:
        if edit["op"] == "set_setting":
            matches = [(tier, criterion) for tier in config["tiers"] for criterion in tier["criteria"]
                       if criterion["id"] == edit["criterion_id"]]
            if len(matches) != 1:
                raise ValueError("criterion_id must identify exactly one existing criterion")
            tier, criterion = matches[0]
            if not tier.get("enabled", True) or not criterion.get("enabled", True):
                raise ValueError("editing a disabled criterion would not change the executed protocol")
            _setting(criterion, edit)
        elif edit["op"] == "reorder_criteria":
            tier = _tier(config, edit["tier_id"])
            old = [criterion["id"] for criterion in tier["criteria"]]
            if sorted(old) != sorted(edit["order"]):
                raise ValueError("order must be a complete permutation of existing criterion ids")
            if old == edit["order"]:
                raise ValueError("reordering makes no effective change")
            by_id = {criterion["id"]: criterion for criterion in tier["criteria"]}
            tier["criteria"] = [by_id[identifier] for identifier in edit["order"]]
        else:
            from molcascade.cascade.models import CriterionConfig
            from molcascade.plugins import create_builtin_registry

            tier = _tier(config, edit["tier_id"])
            inserted = CriterionConfig.model_validate(edit["criterion"]).model_dump(mode="json")
            if inserted["backend"] not in {entry.key for entry in create_builtin_registry()}:
                raise ValueError("inserted backend must identify an exact known built-in plugin")
            if not inserted["enabled"]:
                raise ValueError("inserting a disabled criterion would not change the executed protocol")
            ids = {criterion["id"] for item in config["tiers"] for criterion in item["criteria"]}
            ids.update(step["id"] for step in config.get("finalize", {}).get("steps", []))
            ids.update(config[key]["id"] for key in ("ingest", "standardize") if config.get(key))
            if inserted["id"] in ids:
                raise ValueError("inserted criterion id is already used by the recipe")
            old = [criterion["id"] for criterion in tier["criteria"]]
            if edit["before"] is not None and edit["before"] not in old:
                raise ValueError("before must identify an existing criterion in the selected tier")
            position = old.index(edit["before"]) if edit["before"] is not None else len(old)
            tier["criteria"].insert(position, inserted)
    if canonical(config) == canonical(json.loads(base.configuration)):
        raise ValueError("combined edits make no effective change")
    # Re-check immediately before freezing; freezing itself re-hashes declared files.
    _verify_base(base)
    result = CascadeRecipe.freeze(config, Readout(**json.loads(base.readout_json)),
                                  files=tuple(name for name, _ in base.input_files))
    if result.input_files != base.input_files or result.infrastructure_commit != base.infrastructure_commit:
        raise ValueError("protocol resources changed while constructing the mutation")
    if result.protocol_id == base.protocol_id:
        raise ValueError("mutation makes no effective change")
    return result
