"""Materialize accepted edits without changing the operator's source configuration."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def merge_changes(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = merge_changes(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def materialize(source: str | Path, changes: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> Path:
    """Apply existing-key nested edits or JSON pointers (including list indices).

    Generated files stay beside the source so relative tool and data paths retain their meaning.
    A content-addressed name permits reuse; the original file is never overwritten. Unknown paths
    are errors: an accepted but unapplied edit must not look like a successful policy update.
    """

    source = Path(source).resolve()
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        config = json.loads(text)
    else:
        import yaml

        config = yaml.safe_load(text)
    if not isinstance(config, dict):
        raise ValueError("a cascade configuration must be an object")

    def apply(node: Any, edits: Mapping[str, Any]) -> None:
        for key, value in edits.items():
            if key.startswith("/"):
                parts = [p.replace("~1", "/").replace("~0", "~") for p in key[1:].split("/")]
                parent = node
                try:
                    for part in parts[:-1]:
                        if isinstance(parent, list) and int(part) < 0:
                            raise KeyError(part)
                        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
                    leaf = int(parts[-1]) if isinstance(parent, list) else parts[-1]
                    if isinstance(leaf, int) and leaf < 0:
                        raise KeyError(leaf)
                    parent[leaf]  # Require a pre-existing path.
                    parent[leaf] = copy.deepcopy(value)
                except (KeyError, IndexError, TypeError, ValueError) as error:
                    raise ValueError(f"accepted edit names an unknown config path: {key}") from error
            elif key not in node:
                raise ValueError(f"accepted edit names an unknown config key: {key}")
            elif isinstance(value, Mapping):
                if not isinstance(node[key], dict):
                    raise ValueError(f"config key {key!r} is not an object")
                apply(node[key], value)
            else:
                node[key] = copy.deepcopy(value)

    for update in ([changes] if isinstance(changes, Mapping) else changes):
        apply(config, update)
    payload = json.dumps(config, sort_keys=True, indent=2, allow_nan=False) + "\n"
    digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
    destination = source.with_name(f".{source.stem}.etalon-{digest}.json")
    try:
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError:
        if destination.read_text(encoding="utf-8") != payload:
            raise ValueError(f"materialized configuration was changed: {destination}") from None
    return destination
