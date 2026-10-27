"""Pure literal selection and transport sizing; no source admission or ranking."""
from __future__ import annotations

from etalon.active.schema import canonical

MISSING = object()


def byte_size(value, envelope=None):
    """Count the actual JSON envelope, including Unicode and variable recipes."""
    wrapped = envelope(value) if envelope else value
    return len((wrapped if envelope and isinstance(wrapped, str) else canonical(wrapped)).encode("utf-8"))


def pointer(parent, key):
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def resolve(value, path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("select an explicit JSON Pointer such as /knowledge/0")
    for part in path[1:].split("/"):
        if any(part[i:i + 2] not in {"~0", "~1"} for i, ch in enumerate(part) if ch == "~"):
            raise ValueError("invalid JSON Pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value.get(key, MISSING)
        elif isinstance(value, list) and key.isascii() and key.isdecimal() and str(int(key)) == key and int(key) < len(value):
            value = value[int(key)]
        else:
            return MISSING
    return value


def children(value, path, *, sort_keys=False):
    if isinstance(value, dict):
        return [(pointer(path, k), value[k]) for k in (sorted(value) if sort_keys else value)]
    if isinstance(value, list):
        return [(pointer(path, i), item) for i, item in enumerate(value)]
    return []


def project_json(response, value, pointers, *, offset, limit, max_bytes, envelope=None):
    """Partially deliver an admitted original; preserve the existing selection shape.

    Collections retain original object order. Deferred requests are disjoint from
    delivered rows. Atomic oversized values fail explicitly, never become null.
    """
    selection = response["selection"] = {"format": "json", "items": [],
        "scope": "explicit_pointer_values_only", "unselected_content": "not_delivered"}
    remaining = list(pointers)

    def recipe(paths, start):
        return {"view": "artifact", "selector": {"json": paths}, "offset": start,
                "limit": limit, "max_bytes": max_bytes}

    def update():
        selection["requested_selector_count"] = len(pointers)
        selection["undelivered_selector_count"] = len(remaining)
        selection["next_reads"] = ([recipe(list(remaining), offset)] if remaining else []) + [
            recipe([row["pointer"]], row["next_offset"]) for row in selection["items"]
            if row.get("next_offset") is not None]

    def fits():
        update()
        return byte_size(response, envelope) <= max_bytes

    for path in pointers:
        selected = resolve(value, path)
        row = {"pointer": path, "status": "missing" if selected is MISSING else "selected"}
        remaining.remove(path)
        selection["items"].append(row)
        if isinstance(selected, (dict, list)):
            row.update(value={} if isinstance(selected, dict) else [], offset=offset, limit=limit,
                       total_items=len(selected), next_offset=offset if offset < len(selected) else None)
            keys = list(selected) if isinstance(selected, dict) else list(range(len(selected)))
            for key in keys[offset:offset + limit]:
                if isinstance(selected, dict):
                    row["value"][key] = selected[key]
                else:
                    row["value"].append(selected[key])
                end = offset + len(row["value"])
                row["next_offset"] = end if end < len(selected) else None
                if not fits():
                    if isinstance(selected, dict):
                        row["value"].pop(key)
                    else:
                        row["value"].pop()
                    end = offset + len(row["value"])
                    row["next_offset"] = end if end < len(selected) else None
                    break
            if not row["value"] and offset < len(selected):
                selection["items"].pop()
                remaining.insert(0, path)
                continue
        elif selected is not MISSING:
            row["value"] = selected
        if not fits():
            selection["items"].pop()
            remaining.insert(0, path)
            continue
    update()
    if not selection["items"] or byte_size(response, envelope) > max_bytes:
        raise ValueError("selected JSON exceeds response bound; select child pointers or fewer collection members")
    return response
