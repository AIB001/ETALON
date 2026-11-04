"""Immutable note resources in the existing journal, with a paged summary index.

Only explicitly selected descriptors are loaded. The index is an ordinary set of
Artifact records, so existing journal snapshots and handoff relocation preserve
it without a second database or a process-wide cache.
"""
from __future__ import annotations

from bisect import bisect_left
import json
from pathlib import Path

from etalon.active.schema import canonical, digest
from etalon.runtime.files import _read, _save, file_identity
from .schema import Artifact
from .selection import pointer

RESOURCE = "etalon-note-resource/1"
RESOURCE_V2 = "etalon-note-resource/2"
INDEX = "etalon-note-resource-index/1"
SHARD = "etalon-note-resource-page/1"
FEEDBACK_CASE = "etalon-note-feedback-case/1"
PAGE_SIZE = 64
FEEDBACK_PAGE_SIZE = 16
FEEDBACK_INLINE_CASES = 32
SUMMARY_FIELDS = (
    "kind", "form", "summary", "conditions", "links", "parts", "definitions",
    "requires", "irrelevant", "irrelevant_when", "invalid_when", "applicability", "scope", "available_at",
    "support_refs", "counter_refs", "evidence", "counterevidence", "evidence_refs",
    "family", "family_id", "status", "feedback", "capability_id", "tags", "subject",
)


def _artifact(journal, body, directory, *, parents=(), bindings=None):
    identity = digest(body)
    # Reuse content already archived in this journal, including after relocation
    # or a caller's choice of a different note directory. Verification is local
    # to this call; unchanged progress never enters this function.
    with journal.store.connection() as db:
        existing = db.execute("SELECT key FROM metadata WHERE json_extract(body,'$.bindings.schema')=? "
            "AND json_extract(body,'$.bindings.content_digest')=?",
            (body["schema"], identity)).fetchone()
    if existing:
        ref = {"record_id": existing[0].removeprefix("research:"), "digest": identity}
        _load(journal, ref, body["schema"])
        return ref
    path = Path(directory).absolute() / "resources" / (identity + ".json")
    _save(path, body)
    record = Artifact(str(path), **file_identity(path), source_kind="existing_real_artifact",
        role="diagnostic", bindings={"schema": body["schema"], "content_digest": identity,
            **(bindings or {}),
            **({"count": body["count"]} if body["schema"] == INDEX else {})}, parents=tuple(parents))
    return {"record_id": journal.record(record), "digest": identity}


def _load(journal, ref, schema):
    record = journal.get(ref["record_id"])
    if record.get("bindings", {}).get("schema") != schema or record.get("role") != "diagnostic":
        raise ValueError("note resource record has the wrong schema or role")
    body = _read(journal.artifact_path(Artifact(**record)))
    if body.get("schema") != schema or digest(body) != ref["digest"]:
        raise ValueError("note resource content differs from its reference")
    return body


def _reader(journal):
    """Reuse verified immutable objects only inside one resource read."""
    loaded = {}

    def read(ref, schema):
        key = (ref["record_id"], ref["digest"], schema)
        if key not in loaded:
            loaded[key] = _load(journal, ref, schema)
        return loaded[key]

    return read


def _inline(note):
    resources = note.get("resources", {})
    if not isinstance(resources, dict) or any(not isinstance(k, str) or not k
            or not isinstance(v, dict) for k, v in resources.items()):
        raise ValueError("resources must be named descriptions")
    return resources


def _summary(name, description, ref=None):
    row = {"name": name, "descriptor_digest": digest(description),
           **{k: description[k] for k in SUMMARY_FIELDS if k in description}}
    if ref is not None:
        row.update(ref)
    if isinstance(description.get("execution"), dict):
        row["operation"] = description["execution"].get("operation")
    return row


def _manifest(journal, note, *, _read_body=None):
    ref = note["resources_ref"]
    body = (_read_body or _reader(journal))(ref, INDEX)
    if body["count"] != ref["count"]:
        raise ValueError("note resource count differs from its index")
    return body


def _families(row):
    value = row.get("family")
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return set(value)
    return set()


def _family_names(families):
    if families is None:
        return None
    if (not isinstance(families, (list, tuple)) or any(not isinstance(f, str) or not f for f in families)
            or len(set(families)) != len(families)):
        raise ValueError("resource families must be distinct nonempty strings")
    return set(families)


def resource_families(journal, note):
    """Read explicit family counts; no hierarchy or applicability is inferred."""
    if "resources_ref" in note:
        index = _manifest(journal, note)
        if "families" in index:
            return {"families": [{"name": name, "count": count} for name, count in sorted(index["families"].items())],
                    "unclassified": index["unclassified"], "total_items": index["count"]}
    counts, total, unclassified, offset = {}, 0, 0, 0
    while True:
        page = resource_page(journal, note, offset=offset, limit=1000)
        total += len(page["items"])
        for row in page["items"]:
            names = _families(row)
            unclassified += not names
            for name in names:
                counts[name] = counts.get(name, 0) + 1
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    return {"families": [{"name": name, "count": count} for name, count in sorted(counts.items())],
            "unclassified": unclassified, "total_items": total}


def resource_page(journal, note, *, offset=0, limit=20, names=None, families=None, _read_body=None):
    """Return sorted summary rows without loading descriptor bodies.

    Rows contain name, descriptor_digest, declaration fields, and (for archived
    resources) record_id/digest. Offset refers to names, independent of kinds.
    Legacy inline notes use the same shape without changing their stored bytes.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("resource pages require offset >= 0 and limit 1..1000")
    read = _read_body or _reader(journal)
    chosen_families = _family_names(families)
    if names is not None:
        if (not isinstance(names, (list, tuple)) or any(not isinstance(n, str) or not n for n in names)
                or len(set(names)) != len(names)):
            raise ValueError("resource names must be distinct nonempty strings")
        selected, missing = [], []
        for name in sorted(names):
            try:
                row = resource_row(journal, note, name, _read_body=read)
                if chosen_families is None or _families(row) & chosen_families:
                    selected.append(row)
            except ValueError as exc:
                if not str(exc).startswith("note resource is absent:"):
                    raise
                missing.append(name)
        rows = selected[offset:offset + limit]
        end = offset + len(rows)
        return {"items": rows, "total_items": len(selected), "next_offset": end if end < len(selected) else None,
                "missing_names": missing}
    if "resources_ref" not in note:
        values = _inline(note)
        names = sorted(name for name, row in values.items()
                       if chosen_families is None or _families(row) & chosen_families)
        rows = [_summary(name, values[name]) for name in names[offset:offset + limit]]
        total = len(names)
    else:
        index = _manifest(journal, note, _read_body=read)
        total, rows = (index["count"] if chosen_families is None else 0), []
        for shard in index["pages"]:
            if chosen_families is not None:
                body = None
                if "families" in shard:
                    positions = sorted({position for name in chosen_families
                                        for position in shard["families"].get(name, [])})
                else:
                    body = read(shard, SHARD)
                    positions = [i for i, row in enumerate(body["items"]) if _families(row) & chosen_families]
                start, total = total, total + len(positions)
                if not positions or total <= offset or start >= offset + limit:
                    continue
                body = body if body is not None else read(shard, SHARD)
                rows.extend(body["items"][i] for i in positions[max(offset - start, 0):offset + limit - start])
                continue
            start, end = shard["offset"], shard["offset"] + shard["count"]
            if end <= offset or start >= offset + limit:
                continue
            body = read(shard, SHARD)
            rows.extend(body["items"][max(offset - start, 0): min(offset + limit - start, shard["count"])])
    end = offset + len(rows)
    return {"items": rows, "total_items": total, "next_offset": end if end < total else None}


def resource_row(journal, note, name, *, _read_body=None):
    """Read one summary by its literal resource name, without its body."""
    if "resources_ref" not in note:
        resources = _inline(note)
        if name not in resources:
            raise ValueError("note resource is absent: " + name)
        return _summary(name, resources[name])
    read = _read_body or _reader(journal)
    index = _manifest(journal, note, _read_body=read)
    pages = index["pages"]
    position = bisect_left([page["last"] for page in pages], name)
    if position < len(pages) and pages[position]["first"] <= name:
        for row in read(pages[position], SHARD)["items"]:
            if row["name"] == name:
                return row
    raise ValueError("note resource is absent: " + name)


def resource_identity(journal, note, name):
    """Content identity stays stable when only the surrounding note changes."""
    row = resource_row(journal, note, name)
    return {"name": name, "digest": row["descriptor_digest"]}


def resource_descriptor(journal, note, name):
    """Load and verify only the requested descriptor, not its source originals."""
    row = resource_row(journal, note, name)
    if "resources_ref" not in note:
        return note["resources"][name]
    return _description(journal, row)


def feedback_case_page(journal, note, name, *, offset=0, limit=20):
    """Read a bounded lexical page of cases, without expanding other case bodies.

    V2 verifies the descriptor artifact, case index/pages and selected cases.
    It does not reconstruct the logical descriptor digest or verify all cases.
    Legacy storage still needs its one complete descriptor artifact read.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("feedback pages require offset >= 0 and limit 1..100")
    read = _reader(journal)
    row = resource_row(journal, note, name, _read_body=read)
    if "resources_ref" not in note:
        description, body = note["resources"][name], {}
    else:
        schema = row.get("storage_schema", RESOURCE)
        if schema not in {RESOURCE, RESOURCE_V2}:
            raise ValueError("unsupported note resource storage schema")
        body = read(row, schema)
        description = body["description"]
    if description.get("form") != "resource-feedback":
        raise ValueError("selected resource is not use feedback")
    if "cases_ref" not in body:
        if digest(description) != row["descriptor_digest"]:
            raise ValueError("note resource descriptor digest differs from its index")
        cases = description.get("cases", {})
        keys = sorted(cases)
        selected = {key: cases[key] for key in keys[offset:offset + limit]}
        total, verification = len(keys), "complete_descriptor"
    else:
        page = resource_page(journal, {"resources_ref": body["cases_ref"]},
                             offset=offset, limit=limit, _read_body=read)
        selected = {}
        for item in page["items"]:
            case = read(item, FEEDBACK_CASE)["case"]
            if digest(case) != item["case_digest"] or item["name"] in selected:
                raise ValueError("feedback case differs from its index")
            selected[item["name"]] = case
        total, verification = page["total_items"], "selected_case_artifacts_and_index"
    end = offset + len(selected)
    return {"cases": selected, "total_items": total,
            "next_offset": end if end < total else None,
            "resource_version": row["descriptor_digest"], "verification": verification,
            "summary": description.get("feedback", {})}


def _description(journal, row, *, _read_body=None):
    read = _read_body or _reader(journal)
    schema = row.get("storage_schema", RESOURCE)
    if schema not in {RESOURCE, RESOURCE_V2}:
        raise ValueError("unsupported note resource storage schema")
    body = read(row, schema)
    description = body["description"]
    if "cases_ref" in body:
        cases = {}
        index = _manifest(journal, {"resources_ref": body["cases_ref"]}, _read_body=read)
        for page in index["pages"]:
            for item in read(page, SHARD)["items"]:
                case = read(item, FEEDBACK_CASE)["case"]
                if digest(case) != item["case_digest"] or item["name"] in cases:
                    raise ValueError("feedback case differs from its index")
                cases[item["name"]] = case
        if len(cases) != index["count"]:
            raise ValueError("feedback case count differs from its index")
        description = {**description, "cases": cases}
    if digest(description) != row["descriptor_digest"]:
        raise ValueError("note resource descriptor digest differs from its index")
    return description


def _store_cases(journal, cases, directory, previous=None, *, _read_body=None):
    """Small copy-on-write pages; case bodies and unchanged pages stay shared."""
    read = _read_body or _reader(journal)
    index = _manifest(journal, {"resources_ref": previous}, _read_body=read) if previous else {"pages": []}
    old = {row["name"]: row for page in index["pages"] for row in read(page, SHARD)["items"]}
    updates = {}
    for name, case in cases.items():
        identity = digest(case)
        if name in old and old[name]["case_digest"] == identity:
            continue
        ref = _artifact(journal, {"schema": FEEDBACK_CASE, "case": case}, directory)
        updates[name] = {"name": name, "case_digest": identity, **ref}
    if not updates and set(old) == set(cases) and previous:
        return previous
    if not index["pages"] or set(old) - set(cases):
        merged = {**old, **updates}
        rows = [merged[name] for name in sorted(cases)]
        return _store_index(journal, [_store_page(journal, rows[start:start + FEEDBACK_PAGE_SIZE], directory)
            for start in range(0, len(rows), FEEDBACK_PAGE_SIZE)], directory)
    by_page, pages = {}, []
    ends = [page["last"] for page in index["pages"]]
    for name, row in updates.items():
        position = min(bisect_left(ends, name), len(ends) - 1)
        by_page.setdefault(position, {})[name] = row
    for position, page in enumerate(index["pages"]):
        if position not in by_page:
            pages.append(page)
            continue
        rows = {row["name"]: row for row in read(page, SHARD)["items"]}
        rows.update(by_page[position])
        ordered = [rows[name] for name in sorted(rows)]
        # Split a full leaf evenly; 16+1 splits keep copying a mostly empty
        # root index as randomly keyed cases repeatedly enter its full leaf.
        parts = (len(ordered) + FEEDBACK_PAGE_SIZE - 1) // FEEDBACK_PAGE_SIZE
        step = (len(ordered) + parts - 1) // parts
        pages.extend(_store_page(journal, ordered[start:start + step], directory)
                     for start in range(0, len(ordered), step))
    return _store_index(journal, pages, directory)


def _store_description(journal, description, directory, previous_row=None, *, _read_body=None):
    """Keep the public descriptor/digest while storing feedback cases separately."""
    cases = description.get("cases")
    if (description.get("form") != "resource-feedback" or not isinstance(cases, dict)
            or len(cases) <= FEEDBACK_INLINE_CASES
            or any(not isinstance(name, str) or not name or not isinstance(case, dict)
                   for name, case in cases.items())):
        return _artifact(journal, {"schema": RESOURCE, "description": description}, directory)
    identity = digest(description)
    read = _read_body or _reader(journal)
    # A complete note save must reuse an earlier layout, even when its COW page
    # boundaries differ from those of a newly constructed index.
    with journal.store.connection() as db:
        existing = db.execute("SELECT key,body FROM metadata WHERE json_extract(body,'$.bindings.schema')=? "
            "AND json_extract(body,'$.bindings.descriptor_digest')=?", (RESOURCE_V2, identity)).fetchone()
    if existing:
        binding = json.loads(existing[1])["bindings"]
        row = {"record_id": existing[0].removeprefix("research:"), "digest": binding["content_digest"],
               "descriptor_digest": identity, "storage_schema": RESOURCE_V2}
        _description(journal, row, _read_body=read)
        return {key: row[key] for key in ("record_id", "digest", "storage_schema")}
    prior = read(previous_row, previous_row.get("storage_schema", RESOURCE)).get("cases_ref") if previous_row else None
    ref = _store_cases(journal, cases, directory, prior, _read_body=read)
    body = {"schema": RESOURCE_V2, "description": {k: v for k, v in description.items() if k != "cases"},
            "cases_ref": ref}
    ref = _artifact(journal, body, directory, parents=[ref["record_id"]],
                    bindings={"descriptor_digest": identity})
    return {**ref, "storage_schema": RESOURCE_V2}


def _store_page(journal, items, directory):
    families = {}
    for position, row in enumerate(items):
        for name in _families(row):
            families.setdefault(name, []).append(position)
    ref = _artifact(journal, {"schema": SHARD, "items": items}, directory,
                    parents=[item["record_id"] for item in items])
    return {**ref, "count": len(items), "first": items[0]["name"],
            "last": items[-1]["name"], "families": families}


def _store_index(journal, pages, directory):
    indexed, families, unclassified, count = [], {}, 0, 0
    for page in pages:
        indexed.append({**page, "offset": count})
        count += page["count"]
        classified = set()
        for name, positions in page["families"].items():
            families[name] = families.get(name, 0) + len(positions)
            classified.update(positions)
        unclassified += page["count"] - len(classified)
    ref = _artifact(journal, {"schema": INDEX, "count": count, "pages": indexed,
                            "families": families, "unclassified": unclassified}, directory,
                    parents=[page["record_id"] for page in pages])
    return {**ref, "count": count}


def _store_rows(journal, rows, directory):
    pages = [_store_page(journal, rows[start:start + PAGE_SIZE], directory)
             for start in range(0, len(rows), PAGE_SIZE)]
    return _store_index(journal, pages, directory)


def store_resources(journal, note, directory):
    """Replace an inline resource mapping with its immutable compact reference."""
    if "resources" not in note:
        if "resources_ref" in note:
            ref = note["resources_ref"]
            if (not isinstance(ref, dict) or set(ref) != {"record_id", "digest", "count"}
                    or type(ref["count"]) is not int or ref["count"] < 0):
                raise ValueError("invalid note resource index reference")
            record = journal.get(ref["record_id"])
            binding = record.get("bindings", {})
            if (record.get("role") != "diagnostic" or binding.get("schema") != INDEX
                    or binding.get("content_digest") != ref["digest"]
                    or binding.get("count", ref["count"]) != ref["count"]):
                raise ValueError("note resource reference differs from its archived index")
            if "count" not in binding:
                _manifest(journal, note)
        return note
    rows = []
    for name, description in sorted(_inline(note).items()):
        ref = _store_description(journal, description, directory)
        rows.append(_summary(name, description, ref))
    return {**{k: v for k, v in note.items() if k != "resources"},
            "resources_ref": _store_rows(journal, rows, directory)}


def merge_resources(journal, note, changes, directory):
    """Copy changed pages; retain unchanged page and descriptor identities."""
    changes = _inline({"resources": changes})
    if not changes:
        return note
    if "resources_ref" not in note:
        return store_resources(journal, {**note, "resources": {**_inline(note), **changes}}, directory)
    read = _reader(journal)
    index = _manifest(journal, note, _read_body=read)
    if index["pages"] and all("families" in page for page in index["pages"]):
        by_page = {}
        ends = [page["last"] for page in index["pages"]]
        for name, description in changes.items():
            position = min(bisect_left(ends, name), len(ends) - 1)
            by_page.setdefault(position, {})[name] = description
        pages, changed = [], False
        for position, page in enumerate(index["pages"]):
            if position not in by_page:
                pages.append(page)
                continue
            rows = {row["name"]: row for row in read(page, SHARD)["items"]}
            page_changed = False
            for name, description in by_page[position].items():
                if name in rows and rows[name]["descriptor_digest"] == digest(description):
                    continue
                ref = _store_description(journal, description, directory, rows.get(name), _read_body=read)
                rows[name] = _summary(name, description, ref)
                page_changed = changed = True
            if page_changed:
                ordered = [rows[name] for name in sorted(rows)]
                pages.extend(_store_page(journal, ordered[start:start + PAGE_SIZE], directory)
                             for start in range(0, len(ordered), PAGE_SIZE))
            else:
                pages.append(page)
        return {**note, "resources_ref": _store_index(journal, pages, directory)} if changed else note
    # Older indices did not store family positions; migrate on a resource change.
    rows, offset = {}, 0
    while True:
        page = resource_page(journal, note, offset=offset, limit=1000, _read_body=read)
        rows.update({row["name"]: row for row in page["items"]})
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    for name, description in changes.items():
        if name in rows and rows[name]["descriptor_digest"] == digest(description):
            continue
        ref = _store_description(journal, description, directory, rows.get(name), _read_body=read)
        rows[name] = _summary(name, description, ref)
    return {**note, "resources_ref": _store_rows(journal, [rows[k] for k in sorted(rows)], directory)}


class ResourceCollection(dict):
    """Per-read lazy mapping used only inside bounded note selection.

    It is never serialized or cached across calls. A collection page loads only
    its selected bodies; a nested JSON Pointer loads exactly one descriptor.
    """
    def __init__(self, journal, note):
        self.journal, self.note = journal, note
        self._read_body = _reader(journal)

    def __len__(self):
        return self.note["resources_ref"]["count"]

    def get(self, key, default=None):
        try:
            row = resource_row(self.journal, self.note, key, _read_body=self._read_body)
        except ValueError as exc:
            if str(exc).startswith("note resource is absent:"):
                return default
            raise
        return _description(self.journal, row, _read_body=self._read_body)

    def page(self, parent, offset, limit):
        return [(pointer(parent, row["name"]), _description(self.journal, row, _read_body=self._read_body))
                for row in resource_page(self.journal, self.note, offset=offset, limit=limit,
                                         _read_body=self._read_body)["items"]]


def expand_resources(journal, note, *, lazy=False):
    if "resources_ref" not in note:
        return note
    collection = ResourceCollection(journal, note)
    if lazy:
        resources = collection
    else:
        resources, offset = {}, 0
        while offset < len(collection):
            page = resource_page(journal, note, offset=offset, limit=1000, _read_body=collection._read_body)
            for row in page["items"]:
                resources[row["name"]] = _description(journal, row, _read_body=collection._read_body)
            offset += len(page["items"])
    return {**{k: v for k, v in note.items() if k != "resources_ref"}, "resources": resources}
