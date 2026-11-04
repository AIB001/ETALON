"""Optional condition queries and use feedback over ordinary note resources.

Declarations can suggest whole or partial reuse, never scientific suitability.
Nothing here schedules execution, requires review, or searches historical notes.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path

from etalon.active.schema import digest
from .evidence_refs import EvidenceError
from .meta_context import (
    PROFILE, _check, _definition_map, _strings, compare_part_conditions, make_task_profile,
)


def normalize_query(query):
    """Accept the existing task profile or its small condition-query projection."""
    if query is None:
        return None
    if not isinstance(query, dict):
        raise EvidenceError("resource query must be an object")
    if query.get("schema") == PROFILE:
        _check(query, PROFILE)
        if make_task_profile(**{k: v for k, v in query.items() if k not in {"schema", "digest"}}) != query:
            raise EvidenceError("invalid task profile")
        query = {k: query[k] for k in ("conditions", "critical_conditions", "definitions", "available_stages") if k in query}
    if set(query) - {"conditions", "critical_conditions", "definitions", "available_stages", "intent"}:
        raise EvidenceError("resource query accepts conditions, critical_conditions, definitions, available_stages and intent")
    conditions = query.get("conditions", {})
    if (not isinstance(conditions, dict) or len(conditions) > 64 or
            any(not isinstance(k, str) or not k or v is not None and
                (not isinstance(v, str) or not v) for k, v in conditions.items())):
        raise EvidenceError("query conditions require bounded names and strings or null unknowns")
    critical = query.get("critical_conditions", sorted(conditions))
    _strings(critical)
    if len(critical) > 64:
        raise EvidenceError("too many critical conditions")
    conditions = {**conditions, **{k: None for k in critical if k not in conditions}}
    stages = query.get("available_stages")
    if stages is not None:
        _strings(stages)
    intent = query.get("intent", "use")
    if intent not in {"use", "reference"}:
        raise EvidenceError("query intent must be use or reference")
    result = {"conditions": conditions, "critical_conditions": list(critical),
              "definitions": _definition_map(query.get("definitions", {}), unknown=True), "intent": intent}
    if stages is not None:
        result["available_stages"] = list(stages)
    return result


def _status(checks):
    return "conflict" if "conflict" in checks.values() else "unknown" if "unknown" in checks.values() else "match"


def resource_applicability(resource, query):
    """Return all part checks; contrary and unknown parts are never filtered out.

    Parts use meta_context's existing requires/irrelevant/definition semantics.
    Flat legacy declarations become a whole part with unknown missing metadata.
    Suggested dispositions are advisory; direct-use means declarations matched.
    """
    profile = normalize_query(query)
    if profile is None:
        return None
    if not isinstance(resource, dict):
        raise EvidenceError("resource declaration must be an object")
    declaration = resource.get("descriptor") or resource
    parts = declaration.get("parts") or [{"part_id": "whole",
        "conditions": declaration.get("conditions") or
            (declaration.get("declared_scope") or {}).get("conditions", {}),
        "requires": declaration.get("requires", []), "irrelevant": declaration.get("irrelevant", []),
        "definitions": declaration.get("definitions", {}),
        "available_at": declaration.get("available_at")}]
    if not isinstance(parts, list):
        raise EvidenceError("resource parts must be a list")
    reference = profile["intent"] == "reference" or declaration.get("kind") in {"experience", "claim", "evidence"}
    candidates = []
    for part in parts:
        checks = compare_part_conditions(profile, part)
        status = _status(checks)
        disposition = "reference" if reference else "direct-use" if status == "match" else "adapt"
        candidates.append({"part_id": part["part_id"], "status": status,
                           "disposition": disposition, "checks": checks})
    counts = {status: sum(p["status"] == status for p in candidates) for status in ("match", "conflict", "unknown")}
    status = next((name for name, count in counts.items() if count == len(candidates)), "mixed")
    disposition = "reference" if reference else "direct-use" if status == "match" else "adapt"
    return {"status": status, "disposition": disposition, "parts": candidates,
            "coverage": {"part_count": len(candidates), **counts},
            "scientific_suitability": "not_inferred"}


def _cost(value):
    if value is None:
        return None
    if not isinstance(value, dict) or any(not isinstance(k, str) or not k for k in value):
        raise ValueError("cost requires named measured units or null")
    if any(v is not None and (type(v) not in {int, float} or not math.isfinite(v) or v < 0) for v in value.values()):
        raise ValueError("cost values must be nonnegative finite numbers or null unknowns")
    return dict(value)


def _reference_set(refs):
    """Keep exact observed pins, with deterministic ordering and no duplicates."""
    unique = {digest(ref): ref for ref in refs}
    return [unique[key] for key in sorted(unique)]


def _original_set(paths, name, *, entry):
    from .notes import entry_evidence_paths, original
    if not isinstance(paths, (list, tuple)) or any(not isinstance(path, (str, Path)) for path in paths):
        raise ValueError(name + " must be a list of file paths")
    return _reference_set([original(path) for path in entry_evidence_paths(entry, paths)])


def _feedback_description(previous, receipt):
    """One current conditional summary, with past receipts in ordinary note history."""
    if previous is not None and previous.get("form") != "resource-feedback":
        raise ValueError("feedback name is already used by another resource")
    previous = deepcopy(previous or {})
    cases = previous.get("cases", {})
    case_key = digest({k: receipt[k] for k in ("subject", "conditions", "definitions")})
    case = cases.setdefault(case_key, {k: deepcopy(receipt[k]) for k in ("subject", "conditions", "definitions")})
    counts = case.setdefault("outcome_counts", {})
    outcome = receipt["outcome"]
    counts[outcome] = counts.get(outcome, 0) + 1
    # One latest receipt for each outcome in each condition/version/part case.
    # A later success never discards a negative or unknown case.
    case.setdefault("latest_outcomes", {})[outcome] = deepcopy(receipt)
    totals = previous.get("feedback", {}).get("outcome_counts", {})
    totals[outcome] = totals.get(outcome, 0) + 1
    return {"kind": "experience", "form": "resource-feedback", "subject": receipt["subject"],
            "summary": "Use feedback for " + receipt["subject"]["resource_id"] + ": " +
                ", ".join(str(count) + " " + name for name, count in sorted(totals.items())),
            "links": [{"resource_id": receipt["subject"]["resource_id"], "relation": "feedback_for"}],
            "feedback": {"outcome_counts": totals, "case_count": len(cases),
                         "latest_usage_id": receipt["usage_id"], "scientific_suitability": "not_inferred"},
            "cases": cases, "last_receipt": receipt}


def record_resource_feedback(entry, resource_id, *, usage_id, resource_version, part_ids,
                             conditions, outcome, summary, evidence=(), counterevidence=(),
                             cost=None, definitions=None, branch_id=None, resource_entry=None):
    """Merge a small conditional feedback resource through patch_progress.

    resource_version is the descriptor digest returned by the catalog. Use its
    pinned resource_entry when reporting a version replaced in the current note.
    usage_id names one observation: replay is a no-op even after later progress.
    Outcomes are producer declarations, not an independent method evaluation.
    """
    from .notes import entry_value

    for name, value in (("resource_id", resource_id), ("usage_id", usage_id), ("summary", summary)):
        if not isinstance(value, str) or not value.strip() or len(value) > (2048 if name == "summary" else 160):
            raise ValueError(name + " must be a nonempty bounded string")
    _definition_map({"resource": resource_version})
    _strings(part_ids)
    if not part_ids:
        raise ValueError("feedback must identify the used parts")
    query = normalize_query({"conditions": conditions, "definitions": definitions or {}})
    if outcome not in {"positive", "negative", "unknown", "failed", "no_change"}:
        raise ValueError("feedback outcome must be positive, negative, unknown, failed or no_change")
    receipt = {"usage_id": usage_id, "subject": {"resource_id": resource_id,
        "version": resource_version, "parts": sorted(part_ids)}, "conditions": query["conditions"],
        "definitions": query["definitions"], "outcome": outcome, "summary": summary,
        "evidence": _original_set(evidence, "evidence", entry=entry),
        "counterevidence": _original_set(counterevidence, "counterevidence", entry=entry), "cost": _cost(cost)}
    if resource_entry is not None:
        source_pin = entry_value(resource_entry)
        receipt["resource_entry"] = {k: source_pin[k] for k in
            ("schema", "database", "task_id", "branch_id", "record_id", "workspace", "source_id",
             "memory_scope", "definitions", "definitions_digest") if k in source_pin}
    return _publish_feedback(entry, receipt, branch_id)


def _publish_feedback(entry, receipt, branch_id):
    from etalon.runtime.files import _consumer_lock, _read
    from .method_invocations import _published_ancestor
    from .notes import (SCHEMA, SHARED_SCOPE, entry_journal, entry_value,
                        patch_progress, publish_note, read_entry, read_note)
    from .note_resources import resource_descriptor, resource_identity

    entry = Path(entry).absolute()
    locator = _read(entry)
    if isinstance(locator, dict) and set(locator) == {"entry"}:
        target = Path(locator["entry"])
        entry = target if target.is_absolute() else entry.parent / target
    value = entry_value(entry)
    branch = branch_id or value["branch_id"]
    journal = entry_journal(value)
    resource_id = receipt["subject"]["resource_id"]
    feedback_id = "feedback:" + resource_id
    legacy_id = "resource-use:" + receipt["usage_id"]
    # Hash the retry namespace so arbitrary usage IDs cannot collide with its
    # rebase suffix, or become SQL LIKE wildcards. Legacy exact IDs remain read.
    publication = "resource-use-v2:" + digest(receipt["usage_id"])
    lock = Path(entry).absolute().with_name(Path(entry).name + ".resource-feedback")
    with _consumer_lock(lock):
        for attempt in range(3):
            current = entry_value(entry)
            if any(current.get(key) != value.get(key) for key in
                   ("task_id", "database", "workspace", "source_id", "memory_scope")):
                raise ValueError("feedback entry changed task or journal")
            context = read_entry(current, branch_id=branch, verify_originals=False, expand_resources=False)
            head = context["record_id"]
            with journal.store.connection() as db:
                rows = db.execute("SELECT key FROM metadata WHERE json_extract(body,'$.bindings.schema')=? "
                    "AND json_extract(body,'$.bindings.task_id')=? AND json_extract(body,'$.bindings.branch_id')=? "
                    "AND (json_extract(body,'$.bindings.publication_id') IN (?,?) "
                    "OR json_extract(body,'$.bindings.publication_id') LIKE ?) "
                    "AND COALESCE(json_extract(body,'$.bindings.memory_scope'),'shared')=?",
                    (SCHEMA, value["task_id"], branch, legacy_id, publication, publication + ":%",
                     value.get("memory_scope", SHARED_SCOPE))).fetchall()
            candidates = {row[0].removeprefix("research:") for row in rows}
            family = None
            for rid in candidates:
                prior_note = read_note(journal, rid, verify_originals=False, expand_resources=False)["note"]
                prior = resource_descriptor(journal, prior_note, feedback_id)
                previous_receipt = deepcopy(prior.get("last_receipt"))
                if isinstance(previous_receipt, dict):
                    for name in ("evidence", "counterevidence"):
                        previous_receipt[name] = _reference_set(previous_receipt.get(name, []))
                if previous_receipt != receipt:
                    raise ValueError("usage_id already records different resource feedback")
                family = prior.get("family")
            visible = _published_ancestor(journal, head, candidates)
            if visible is not None:
                return {"ok": True, "record_id": visible, "feedback_resource_id": feedback_id,
                        "replayed": True, "execution_performed": False}
            resumable = next((rid for rid in candidates if head in journal.get(rid).get("parents", ())), None)
            try:
                if resumable is not None:
                    publish_note(entry, journal, resumable)
                    return {"ok": True, "record_id": resumable, "feedback_resource_id": feedback_id,
                            "replayed": True, "recovered": True, "execution_performed": False}
                if not candidates:
                    source_value = receipt.get("resource_entry", current)
                    source = read_entry(source_value, branch_id=None if "resource_entry" in receipt else branch,
                                        verify_originals=False, expand_resources=False)
                    source_journal = entry_journal(source_value)
                    if resource_identity(source_journal, source["note"], resource_id)["digest"] != receipt["subject"]["version"]:
                        raise ValueError("resource version changed; supply its pinned resource_entry")
                    description = resource_descriptor(source_journal, source["note"], resource_id)
                    family = description.get("family")
                    parts = {part["part_id"] for part in description.get("parts", [])} or {"whole"}
                    if not set(receipt["subject"]["parts"]) <= parts:
                        raise ValueError("feedback names a part absent from the exact resource version")
                try:
                    previous = resource_descriptor(journal, context["note"], feedback_id)
                except ValueError as exc:
                    if str(exc) != "note resource is absent: " + feedback_id:
                        raise
                    previous = None
                # Only this receipt is replayed onto the current head. Its
                # unpublished snapshot cannot replace intervening host facts.
                description = _feedback_description(previous, receipt)
                families = set()
                for labels in (family, previous.get("family") if previous else None):
                    if labels is not None:
                        labels = [labels] if isinstance(labels, str) else labels
                        _strings(labels)
                        families.update(labels)
                if families:
                    description["family"] = next(iter(families)) if len(families) == 1 else sorted(families)
                result = patch_progress(entry, {}, branch_id=branch, expected_record=head,
                    publication_id=publication + ":" + digest(head)[:16] if candidates else publication,
                    resources={feedback_id: description})
                return {**result, "feedback_resource_id": feedback_id, "replayed": bool(candidates),
                        **({"recovered": True} if candidates else {})}
            except ValueError as error:
                if attempt == 2 or not str(error).startswith("entry changed"):
                    raise


def feedback_for_query(description, query, *, offset=0, limit=20, status=None):
    """Page matching cases first, while keeping contrary/unknown coverage visible.

    status optionally selects match, conflict or unknown; it is a view, not an
    adoption decision. All cases remain in the immutable feedback description.
    """
    if (type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100
            or status not in {None, "match", "conflict", "unknown"}):
        raise ValueError("feedback pages require offset >= 0, limit 1..100 and a known status")
    profile = normalize_query(query)
    cases = []
    for case_id, case in sorted(description.get("cases", {}).items()):
        checks = compare_part_conditions(profile or {}, {"conditions": case["conditions"],
            "definitions": case.get("definitions", {})})
        # Use feedback is an observation, with no execution-stage requirement.
        checks.pop("available_at")
        cases.append({"case_id": case_id, "subject": case["subject"], "conditions": case["conditions"],
                      "status": _status(checks), "checks": checks,
                      "outcome_counts": case["outcome_counts"], "latest_outcomes": case["latest_outcomes"]})
    counts = {name: sum(case["status"] == name for case in cases) for name in ("match", "conflict", "unknown")}
    outcomes = {name: {} for name in counts}
    for case in cases:
        for outcome, count in case["outcome_counts"].items():
            bucket = outcomes[case["status"]]
            bucket[outcome] = bucket.get(outcome, 0) + count
    cases.sort(key=lambda case: ({"match": 0, "unknown": 1, "conflict": 2}[case["status"]], case["case_id"]))
    selected = cases if status is None else [case for case in cases if case["status"] == status]
    end = min(offset + limit, len(selected))
    return {"cases": selected[offset:end], "case_count": len(cases), "status_counts": counts,
            "outcome_counts_by_status": outcomes,
            "selected_count": len(selected), "next_offset": end if end < len(selected) else None,
            "status_filter": status, "scientific_suitability": "not_inferred"}


def feedback_read_link(entry, resource_id, *, query=None, resource_version=None, offset=0, limit=20, max_bytes=None):
    """Exact read arguments, reusable by discovery and selected-method delivery."""
    arguments = {"entry": entry, "resource_id": resource_id, "offset": offset, "limit": limit}
    if query is not None:
        arguments["query"] = query
    if resource_version is not None:
        arguments["resource_version"] = resource_version
    if max_bytes is not None:
        arguments["max_bytes"] = max_bytes
    return {"tool": "etalon_resource_feedback", "arguments": arguments}


def read_resource_feedback(entry, resource_id, *, query=None, branch_id=None,
                           resource_version=None, offset=0, limit=20, max_bytes=8192):
    """Deliver a bounded page of conditional experience from one fixed note.

    Offsets address lexical case IDs, not a moving head or a relevance ranking.
    Conditions are evaluated per case; current-version cases sort first within
    this page. Counts explicitly cover this page, never unseen history. Reading
    remains optional, and feedback is not an invocation/adoption permission.
    """
    from .notes import byte_size, entry_value, entry_journal, read_entry
    from .note_resources import feedback_case_page, resource_row
    from .selection import pointer

    if not isinstance(resource_id, str) or not resource_id or len(resource_id) > 160:
        raise ValueError("resource_id must be a nonempty bounded name")
    if type(max_bytes) is not int or not 2048 <= max_bytes <= 65536:
        raise ValueError("feedback response requires max_bytes 2048..65536")
    # Validate even when this note has no feedback.
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("feedback pages require offset >= 0 and limit 1..100")
    normalize_query(query)
    if resource_version is not None:
        _definition_map({"resource": resource_version})
    value = entry_value(entry)
    context = read_entry(value, branch_id=branch_id, verify_originals=False, expand_resources=False)
    details = {k: value[k] for k in ("schema", "database", "task_id", "workspace", "source_id",
               "memory_scope", "definitions_digest") if k in value}
    details.update(branch_id=context["branch_id"], record_id=context["record_id"])
    journal, note = entry_journal(value), context["note"]
    if resource_version is None:
        resource_version = resource_row(journal, note, resource_id)["descriptor_digest"]
    feedback_id = "feedback:" + resource_id
    selector = pointer("/resources", feedback_id)
    result = {"schema": "etalon-resource-feedback/1", "resource_id": resource_id,
              "resource_version": resource_version, "feedback_resource_id": feedback_id,
              "content_ref": details, "pointer": selector, "record_id": context["record_id"],
              "execution_performed": False, "scientific_suitability": "not_inferred"}
    try:
        page = feedback_case_page(journal, note, feedback_id, offset=offset, limit=limit)
    except ValueError as exc:
        if str(exc) != "note resource is absent: " + feedback_id:
            raise
        result.update(status="absent", cases=[], case_count=0, next_offset=None, next_read=None,
                      coverage={"complete": True, "scope": "selected_note"})
        if byte_size(result) > max_bytes:
            raise ValueError("feedback locator exceeds response bound")
        return result

    # Keep the physical page prefix when reducing delivery: no cases are skipped
    # even though display order within that prefix is condition/version aware.
    raw = list(page["cases"].items())
    def render(items, compact=False):
        view = feedback_for_query({"cases": dict(items)}, query, limit=100)
        cases = view["cases"]
        versions = {"same_version": 0, "other_version": 0}
        for case in cases:
            relation = "same_version" if case["subject"]["version"] == resource_version else "other_version"
            case["version_relation"] = relation
            versions[relation] += 1
            case["pointer"] = pointer(pointer(selector, "cases"), case["case_id"])
            if compact:
                # Preserve every outcome sign, measured cost and short lesson.
                # Full receipts, originals and definitions stay at this pointer.
                case["latest_outcomes"] = {name: {key: receipt[key] for key in ("usage_id", "cost") if key in receipt}
                    | {"summary": str(receipt.get("summary", ""))[:600],
                       "summary_truncated": len(str(receipt.get("summary", ""))) > 600}
                    for name, receipt in case["latest_outcomes"].items()}
                case["receipt_details_omitted"] = True
        cases.sort(key=lambda case: (case["version_relation"] != "same_version",
                   {"match": 0, "unknown": 1, "conflict": 2}[case["status"]], case["case_id"]))
        end = offset + len(items)
        next_offset = end if end < page["total_items"] else None
        return {**result, "status": "available", "feedback_version": page["resource_version"],
                "cases": cases, "case_count": page["total_items"],
                "status_counts": view["status_counts"], "outcome_counts_by_status": view["outcome_counts_by_status"],
                "version_counts": versions, "offset": offset, "next_offset": next_offset,
                "next_read": feedback_read_link(details, resource_id, query=query,
                    resource_version=resource_version, offset=next_offset, limit=limit,
                    max_bytes=max_bytes) if next_offset is not None else None,
                "coverage": {"scope": "selected_note_case_page", "counts_scope": "returned_cases",
                    "returned_cases": len(items), "complete": offset == 0 and end >= page["total_items"],
                    "ordering": "lexical_pages_then_version_and_condition_within_page",
                    "verification": page["verification"], "originals": "not_read"}}

    rendered = render(raw)
    if byte_size(rendered) <= max_bytes:
        return rendered
    while raw:
        rendered = render(raw, compact=True)
        if byte_size(rendered) <= max_bytes:
            return rendered
        if len(raw) > 1:
            raw.pop()
            continue
        # One unusually large case cannot stall pagination. Return its exact
        # field pointers and condition result without repeating a giant receipt.
        case = rendered["cases"][0]
        rendered["cases"] = [{key: case[key] for key in
            ("case_id", "pointer", "status", "version_relation")} | {"body_omitted": True}]
        if byte_size(rendered) <= max_bytes:
            return rendered
        break
    raise ValueError("feedback locator/query exceeds response bound; use a larger max_bytes or smaller query")
