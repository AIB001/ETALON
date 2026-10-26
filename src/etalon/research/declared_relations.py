"""One read-local index of explicit relationships in a published source view.

This is disposable metadata, not a new authority, semantic dependency model or
proof that originals were read. Callers retain their ordinary byte verification.
"""
from __future__ import annotations

import json

from etalon.active.schema import canonical, digest
from .evidence import MAX_RECORDS, _relationships
from .evidence_refs import EvidenceError, make_ref, validate_ref
from .read_scope import CURRENT, MAX_CACHE_BYTES


def record_identity(ref):
    """Match declarations across qualified envelopes, retaining each envelope."""
    ref = validate_ref(ref)
    return canonical([ref[k] for k in ("source_id", "record_id", "record_digest")])


def counter_refs(value):
    """Only explicit counter declarations; nested bindings count, prose does not."""
    if isinstance(value, dict) and value.get("schema") != "etalon-evidence-ref/1":
        for key, child in value.items():
            if key == "counter_refs":
                if not isinstance(child, list):
                    raise EvidenceError("counter_refs must be explicit references")
                for ref in child:
                    yield validate_ref(ref)
            elif isinstance(child, (dict, list)):
                yield from counter_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from counter_refs(child)


def checked_row(universe, ref):
    """Validate all supplied identity assertions, not just a node's record ID."""
    ref = validate_ref(ref)
    row = universe.get((ref["source_id"], ref["record_id"]))
    if row is None or digest(row["record"]) != ref["record_digest"]:
        raise EvidenceError("declared reference unavailable, restricted or identity changed")
    if row.get("projection", {}).get("status") == "unavailable":
        raise EvidenceError("declared reference content projection unavailable")
    body = row["record"]
    if "member" in ref:
        members = [m for m in row.get("members", body.get("members", []))
                   if m.get("member") == ref["member"] and m.get("visibility") == "released"]
        if len(members) != 1:
            raise EvidenceError("declared reference member is not uniquely published")
        body = members[0]
    if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (body.get("sha256"), body.get("bytes")):
        raise EvidenceError("declared reference artifact identity mismatch")
    return row


def _build_index(collection, *, dependencies):
    records, coverage, warnings = collection
    universe = {(sid, rid): row for sid, rows in records.items() for rid, row in rows.items()}
    relations, incidence, reverse, issues = {}, {}, {}, []

    def add(relation):
        key = canonical(relation)
        if key in relations:
            return
        if len(relations) >= MAX_RECORDS:
            raise EvidenceError("declared relation index exceeds existing record index bound")
        relations[key] = relation
        for ref in (relation["from_ref"], relation["to_ref"]):
            incidence.setdefault(record_identity(ref), []).append(key)
        reverse.setdefault(record_identity(relation["from_ref"]), []).append(key)

    for (sid, rid), row in sorted(universe.items()):
        owner = make_ref(sid, rid, row["record"])
        counters = {canonical(ref): ref for value in (row["record"], row.get("projected_content", {}))
                    for ref in counter_refs(value)}
        for target in counters.values():
            checked_row(universe, target)
            add({"kind": "refutes", "from_ref": target, "to_ref": owner, "basis_ref": owner})
        if not dependencies:
            continue
        supersedes = {canonical(ref) for value in (row["record"], row.get("projected_content", {}))
                      for ref in value.get("supersedes", []) if isinstance(ref, dict)}
        for target in _relationships(row):
            if isinstance(target, str):
                linked = universe.get((sid, target))
                if linked is None and row["record"].get("schema") == "etalon-method-evaluation/1":
                    encoded = "budget:execution:" + target
                    resource = universe.get((sid, encoded))
                    if resource is not None and resource["record"].get("execution_id") == target:
                        linked, target = resource, encoded
                if linked is None:
                    issues.append({"reason": "legacy_reference_unavailable", "owner_ref": owner,
                                   "legacy_record_id": target})
                    continue
                target = make_ref(sid, target, linked["record"])
            if not isinstance(target, dict):
                continue
            checked_row(universe, target)
            if canonical(target) in counters:
                continue
            # Impact direction is input -> declaring consumer. It is an exact
            # reference dependency, not an inferred causal/scientific relation.
            if canonical(target) in supersedes:
                add({"kind": "supersedes", "from_ref": owner, "to_ref": target, "basis_ref": owner})
            else:
                add({"kind": "declared_dependency", "from_ref": target, "to_ref": owner, "basis_ref": owner})
    return {"relations": relations, "incidence": incidence, "forward": reverse,
            "issues": issues, "record_count": len(universe),
            "source_complete": not warnings and all(c["status"] == "complete" for c in coverage)}


def relation_index(workspace, collection, *, dependencies=False):
    """Share extraction only within a verified read of the same frozen view.

    Source snapshots already bind record/projection content. Live/partial views
    are never admitted. A cache hit never bypasses outer original revalidation.
    Returned dictionaries are copies, so a consumer cannot poison other views.
    """
    _, coverage, warnings = collection
    scope = CURRENT.get()
    reusable = (scope is not None and not warnings and all(c["status"] == "complete"
        and workspace.source(c["source_id"])["identity"]["kind"] == "snapshot_sha256" for c in coverage))
    key = digest({"workspace": workspace.payload, "base": str(workspace.base),
                  "coverage": coverage, "dependencies": dependencies})
    if reusable and key in scope.relation_indexes:
        scope.relation_hits += 1
        return json.loads(scope.relation_indexes[key])
    result = _build_index(collection, dependencies=dependencies)
    if scope is not None:
        scope.relation_builds += 1
    if reusable:
        raw = canonical(result)
        size = len(raw.encode("utf-8"))
        if scope.bytes + size <= MAX_CACHE_BYTES:
            scope.relation_indexes[key] = raw
            scope.bytes += size
    return result
