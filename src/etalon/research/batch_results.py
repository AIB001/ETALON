"""Optional, resumable archival of batch results in an existing ResearchJournal.

No scientific work is submitted or retried. Counts describe the explicit input
inventory; status, scientific identities and consumption remain producer/host
declarations. Ordinary Artifacts retain full originals and bounded chunk indexes.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from etalon.active.schema import canonical, digest
from etalon.host import _consumer_lock
from .components import record_json
from .composition import node_step
from .continuation import record_content
from .evidence_refs import EvidenceError, file_identity, make_ref
from .publication import _copy, _read, _save
from .schema import Artifact

INPUT = "etalon-batch-results-input/1"
RECEIPT = "etalon-batch-results-receipt/1"
SUMMARY = "etalon-batch-results-summary/1"
INDEX = "etalon-batch-results-index/1"
RANGE_INDEX = "etalon-batch-results-index/2"
INDEX_PAGE = "etalon-batch-results-index-page/1"
CHUNK = "etalon-batch-results-chunk/1"
STATUSES = ("completed", "failed", "not_applicable", "censored", "unresolved")
MODES = ("new_execution", "historical_replay", "cache_hit", "not_executed")
ROLES = ("diagnostic", "independent_validation", "sealed_holdout")
CHUNK_ATTEMPTS = 32
INDEX_PAGE_CHUNKS = 32
MAX_CHUNK_BYTES = 256 * 1024
MAX_ATTEMPTS = 100_000
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_ROW_BYTES = 32 * 1024


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise EvidenceError("missing or unsupported batch fields")


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise EvidenceError("bounded nonempty batch text required")


def _names(value):
    if not isinstance(value, list) or len(value) > 64:
        raise EvidenceError("bounded explicit batch name list required")
    for item in value:
        _text(item)
    if len(set(value)) != len(value):
        raise EvidenceError("duplicate batch member name")


def _file(value, *, named=False):
    _fields(value, ("path", "sha256", "bytes", *(('name',) if named else ())), ("role",))
    if named:
        _text(value["name"])
    if not isinstance(value["path"], str) or not Path(value["path"]).is_absolute():
        raise EvidenceError("batch original requires absolute native path")
    # Reuse the kernel's file/provenance validation without recording anything.
    Artifact(value["path"], value["sha256"], value["bytes"], "fixture", value.get("role", "diagnostic"), {})


def _key(value):
    return value["request_id"], value["attempt_id"]


def _read_json(path, maximum=MAX_MANIFEST_BYTES):
    path = Path(path)
    if path.stat().st_size > maximum:
        raise EvidenceError("batch JSON exceeds byte budget")
    raw = path.read_bytes()
    if len(raw) > maximum:
        raise EvidenceError("batch JSON exceeds byte budget")
    return json.loads(raw.decode("utf-8-sig"))


def validate_batch_manifest(manifest):
    """Validate declarations, retaining missing attempts and explicit unknowns."""
    _fields(manifest, ("schema", "batch_id", "task_id", "question", "branch_id", "expected_attempts", "rows"),
            ("scope_limits",))
    if manifest["schema"] != INPUT:
        raise EvidenceError("wrong batch input schema")
    for key in ("batch_id", "task_id", "question", "branch_id"):
        _text(manifest[key])
    if len(canonical(manifest).encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise EvidenceError("batch input exceeds manifest byte budget")
    if not isinstance(manifest["expected_attempts"], list) or not 1 <= len(manifest["expected_attempts"]) <= MAX_ATTEMPTS:
        raise EvidenceError("explicit bounded nonempty expected inventory required")
    expected = set()
    for item in manifest["expected_attempts"]:
        _fields(item, ("request_id", "attempt_id", "required_originals", "required_counterevidence"))
        for key in ("request_id", "attempt_id"):
            _text(item[key])
        for key in ("required_originals", "required_counterevidence"):
            _names(item[key])
        if _key(item) in expected:
            raise EvidenceError("duplicate expected request/attempt identity")
        expected.add(_key(item))
    if not isinstance(manifest["rows"], list) or len(manifest["rows"]) > len(expected):
        raise EvidenceError("rows must be a subset of the frozen expected inventory")
    seen = set()
    for row in manifest["rows"]:
        _fields(row, ("request_id", "attempt_id", "status", "execution_mode", "receipt", "originals",
                      "counterevidence", "actual_consumption", "identities"))
        if _key(row) not in expected or _key(row) in seen:
            raise EvidenceError("unknown or duplicate result request/attempt identity")
        seen.add(_key(row))
        if row["status"] not in STATUSES or row["execution_mode"] not in MODES:
            raise EvidenceError("unsupported batch status or execution mode")
        if row["status"] in {"completed", "failed"} and row["execution_mode"] == "not_executed":
            raise EvidenceError("completed/failed attempt cannot claim not_executed")
        if row["receipt"] is None:
            if row["status"] != "unresolved":
                raise EvidenceError("terminal/censored batch row requires an original receipt")
        else:
            _file(row["receipt"])
        for category in ("originals", "counterevidence"):
            if not isinstance(row[category], list) or len(row[category]) > 64:
                raise EvidenceError("bounded explicit original inventory required")
            for value in row[category]:
                _file(value, named=True)
            if len({v["name"] for v in row[category]}) != len(row[category]):
                raise EvidenceError("duplicate batch original name")
        if not isinstance(row["actual_consumption"], dict) or len(row["actual_consumption"]) > 32:
            raise EvidenceError("explicit measured/unknown consumption object required")
        for unit, value in row["actual_consumption"].items():
            _text(unit)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise EvidenceError("consumption must be nonnegative finite or null")
        if not isinstance(row["identities"], dict) or len(row["identities"]) > 64:
            raise EvidenceError("explicit scientific identity declarations required")
        for key, value in row["identities"].items():
            _text(key)
            _text(value)
        if len(canonical(row).encode("utf-8")) > MAX_ROW_BYTES:
            raise EvidenceError("batch row exceeds byte budget; link a separate original")
    if not isinstance(manifest.get("scope_limits", []), list) or len(manifest.get("scope_limits", [])) > 64:
        raise EvidenceError("bounded explicit scope limits required")
    for limit in manifest.get("scope_limits", []):
        _text(limit)
    return json.loads(canonical(manifest))


def _specs(row):
    if row is None:
        return
    if row["receipt"] is not None:
        yield "receipt", "receipt", row["receipt"]
    for kind in ("originals", "counterevidence"):
        for value in row[kind]:
            yield kind, value["name"], value


def _role(manifest, default="diagnostic"):
    if default not in ROLES:
        raise EvidenceError("explicit supported batch role required")
    return max([default, *(spec.get("role", default) for row in manifest["rows"]
                          for _, _, spec in _specs(row))], key=ROLES.index)


def _ref(journal, source_id, record_id):
    return make_ref(source_id, record_id, journal.get(record_id))


def _archive(journal, spec, directory, source_kind, default_role="diagnostic"):
    identity = {key: spec[key] for key in ("sha256", "bytes")}
    target = directory / "originals" / spec["sha256"]
    _copy(spec["path"], target, identity)
    role = max((spec.get("role", default_role), default_role), key=ROLES.index)
    bindings = {"kind": "batch_original"}
    if role != "diagnostic":
        bindings["visibility"] = "sealed"
    artifact = Artifact(str(target), **identity, source_kind=source_kind, role=role, bindings=bindings)
    journal.record(artifact)
    return {"record_id": artifact.id, "record_digest": digest(artifact.as_dict()),
            "artifact": artifact.as_dict()}


def _json(journal, content, directory, source_id, source_kind, role="diagnostic"):
    bindings = {"schema": content["schema"], "task_id": content.get("task_id", ""),
                "batch_id": content.get("batch_id", ""), "kind": "batch_results"}
    if role == "diagnostic":
        rid = record_json(journal, content, directory, source_kind=source_kind, bindings=bindings)
    else:
        # Do not record a temporary diagnostic copy: even its metadata would
        # publish the existence/counts of sealed confirmation material.
        path = Path(directory).absolute() / (digest(content) + ".json")
        _save(path, content)
        bindings["visibility"] = "sealed"
        rid = journal.record(Artifact(str(path), **file_identity(path), source_kind=source_kind,
                                      role=role, bindings=bindings))
    return _ref(journal, source_id, rid)


def _summary(manifest):
    rows = {_key(row): row for row in manifest["rows"]}
    statuses, modes = Counter(), Counter()
    missing_originals = missing_counters = required_originals = required_counters = 0
    supplied_originals = supplied_counters = unresolved = missing = undeclared_consumption = 0
    # A unit absent in a row is unknown, even if other rows measured that unit.
    units = sorted({unit for row in rows.values() for unit in row["actual_consumption"]})
    consumption = {mode: {unit: {"known_sum": 0, "known_rows": 0, "unknown_rows": 0} for unit in units}
                   for mode in MODES}
    for expected in manifest["expected_attempts"]:
        required_originals += len(expected["required_originals"])
        required_counters += len(expected["required_counterevidence"])
        row = rows.get(_key(expected))
        original_names = {value["name"] for value in row["originals"]} if row else set()
        counter_names = {value["name"] for value in row["counterevidence"]} if row else set()
        missing_originals += len(set(expected["required_originals"]) - original_names)
        missing_counters += len(set(expected["required_counterevidence"]) - counter_names)
        if row is None:
            missing += 1
            continue
        statuses[row["status"]] += 1
        modes[row["execution_mode"]] += 1
        unresolved += row["status"] == "unresolved"
        supplied_originals += len(row["originals"])
        supplied_counters += len(row["counterevidence"])
        undeclared_consumption += not bool(row["actual_consumption"])
        for unit in units:
            value = row["actual_consumption"].get(unit)
            cell = consumption[row["execution_mode"]][unit]
            cell["unknown_rows" if value is None else "known_rows"] += 1
            if value is not None:
                cell["known_sum"] += value
    for by_unit in consumption.values():
        for cell in by_unit.values():
            if not math.isfinite(cell["known_sum"]):
                raise EvidenceError("batch consumption sum overflow")
            cell["total"] = cell["known_sum"] if cell["known_rows"] and not cell["unknown_rows"] else None
    return {"schema": SUMMARY, "batch_id": manifest["batch_id"], "task_id": manifest["task_id"],
        "branch_id": manifest["branch_id"], "manifest_digest": digest(manifest),
        "expected": len(manifest["expected_attempts"]), "reported": len(rows), "missing": missing,
        "status_counts": {name: statuses[name] for name in STATUSES},
        "execution_mode_counts": {name: modes[name] for name in MODES},
        "declared_executed": modes["new_execution"],
        "declared_originals": supplied_originals, "declared_counterevidence": supplied_counters,
        "required_originals": required_originals, "required_counterevidence": required_counters,
        "missing_required_originals": missing_originals, "missing_required_counterevidence": missing_counters,
        "coverage": "complete" if not (missing or unresolved or missing_originals or missing_counters) else "incomplete",
        "actual_consumption_by_mode": consumption, "missing_attempt_consumption": None if missing else "none_missing",
        "consumption_undeclared_rows": undeclared_consumption,
        "cost_basis": "declared receipt values; disjoint units are not added; no ledger settlement or new charge",
        "status_basis": "producer/host declared; original-byte verification does not certify scientific status",
        "counterevidence_scope": "explicit required inventory only; undisclosed counterevidence completeness not certified",
        "scope_limits": manifest.get("scope_limits", []), "scientific_truth": "not_certified",
        "adoption": "not_performed", "automatic_retry": False}


def ingest_batch_results(journal, manifest, directory, *, source_id, source_kind, role=None):
    """Archive one immutable block. Retry with the same directory and batch_id.

    Existing complete copies survive disappearance of producer files. A different
    manifest for this batch_id conflicts; later result arrivals need a new block
    identity. Scientific requests are neither created nor invoked by this API.
    """
    manifest = validate_batch_manifest(manifest)
    _text(source_id)
    if source_kind not in {"fixture", "existing_real_artifact", "new_execution"}:
        raise EvidenceError("explicit batch source_kind required")
    default_role = "diagnostic" if role is None else role
    artifact_role = _role(manifest, default_role)
    directory = Path(directory).resolve()
    root = directory / digest({"campaign": str(journal.store.path), "batch_id": manifest["batch_id"]})[:24]
    root.mkdir(parents=True, exist_ok=True)
    request = {"schema": "etalon-batch-results-intent/1", "manifest": manifest,
               "campaign": str(journal.store.path), "source_id": source_id, "source_kind": source_kind}
    if role is not None:
        request["default_role"] = default_role
    with _consumer_lock(root / "ingest"):
        intent_path = root / "intent.json"
        _save(intent_path, request)  # Immutable conflict detection precedes any DB append.
        receipt_path = root / "receipt.json"
        if receipt_path.exists():
            result = _read(receipt_path)
            verify_batch_results(journal, result)
            return result
        try:
            manifest_ref = _json(journal, manifest, root / "content", source_id, source_kind, artifact_role)
            rows = {_key(row): row for row in manifest["rows"]}
            attempts = []
            for expected in manifest["expected_attempts"]:
                row = rows.get(_key(expected))
                originals = []
                for kind, name, spec in _specs(row):
                    originals.append({"kind": kind, "name": name, "original": spec,
                                      **_archive(journal, spec, directory, source_kind, default_role)})
                attempts.append({"expected": expected, "row": row, "archives": originals})
            chunks, pending = [], []

            def save_chunk():
                content = {"schema": CHUNK, "batch_id": manifest["batch_id"], "task_id": manifest["task_id"],
                    "ordinal": len(chunks), "offset": sum(item["attempts"] for item in chunks), "attempts": list(pending)}
                ref = _json(journal, content, root / "content", source_id, source_kind, artifact_role)
                chunks.append({"ordinal": len(chunks), "offset": content["offset"],
                               "attempts": len(pending), "ref": ref})
                pending.clear()

            for attempt in attempts:
                # Bound by both bytes and row count, without raising existing read limits.
                estimate = len(canonical({"schema": CHUNK, "batch_id": manifest["batch_id"],
                    "task_id": manifest["task_id"], "ordinal": len(chunks), "offset": 0,
                    "attempts": [*pending, attempt]}).encode("utf-8")) * 2
                if pending and (len(pending) >= CHUNK_ATTEMPTS or estimate > MAX_CHUNK_BYTES):
                    save_chunk()
                if len(canonical(attempt).encode("utf-8")) * 2 > MAX_CHUNK_BYTES:
                    raise EvidenceError("archived row exceeds chunk budget; narrow its explicit original inventory")
                pending.append(attempt)
            if pending:
                save_chunk()
            # Index pages keep even a large panel navigable through ordinary bounded reads.
            pages = []
            for offset in range(0, len(chunks), INDEX_PAGE_CHUNKS):
                page = {"schema": INDEX_PAGE, "batch_id": manifest["batch_id"],
                    "task_id": manifest["task_id"], "chunks": chunks[offset:offset + INDEX_PAGE_CHUNKS]}
                ref = _json(journal, page, root / "content", source_id, source_kind, artifact_role)
                pages.append({"first_chunk": offset, "chunk_count": len(page["chunks"]),
                    "attempt_offset": page["chunks"][0]["offset"],
                    "attempt_count": sum(c["attempts"] for c in page["chunks"]), "ref": ref})
            index = {"schema": RANGE_INDEX, "batch_id": manifest["batch_id"], "task_id": manifest["task_id"],
                "manifest_ref": manifest_ref, "expected": len(attempts), "chunk_count": len(chunks), "pages": pages}
            index_ref = _json(journal, index, root / "content", source_id, source_kind, artifact_role)
            summary = {**_summary(manifest), "index_ref": index_ref, "manifest_ref": manifest_ref}
            summary_ref = _json(journal, summary, root / "content", source_id, source_kind, artifact_role)
            step = node_step(task_id=manifest["task_id"], question=manifest["question"], branch_id=manifest["branch_id"],
                node_id="batch-results:" + manifest["batch_id"], state="completed" if summary["coverage"] == "complete" else "partial",
                reason="Archive explicit batch evidence and count missing/failed/N/A results; no scientific execution or adoption",
                input_refs=[manifest_ref], output_refs=[index_ref, summary_ref], receipt_refs=[summary_ref])
            step_id = record_content(journal, step, root / "content", source_id=source_id, source_kind=source_kind,
                                     role=artifact_role)
            result = {"schema": RECEIPT, "batch_id": manifest["batch_id"], "task_id": manifest["task_id"],
                "campaign": str(journal.store.path), "source_id": source_id, "source_kind": source_kind,
                "default_role": default_role,
                "receipt_path": str(receipt_path), "intent_identity": file_identity(intent_path),
                "manifest_ref": manifest_ref, "index_ref": index_ref, "summary_ref": summary_ref,
                "step_ref": _ref(journal, source_id, step_id), "execution": "not_performed_by_ingestion",
                "new_scientific_charge": 0, "ingestion_compute_cost": None, "automatic_retry": False}
            verify_batch_results(journal, result)
            _save(receipt_path, result)
            return result
        except Exception as exc:
            # Append-only diagnostics, including failures superseded by successful recovery.
            failure = {"schema": "etalon-batch-results-failure/1", "batch_id": manifest["batch_id"],
                "error_type": type(exc).__name__, "reason": str(exc)[:500], "automatic_retry": False}
            _save(root / "failures" / (digest(failure) + ".json"), failure)
            raise


def _load(journal, ref):
    from .evidence_refs import validate_ref
    validate_ref(ref)
    body = journal.get(ref["record_id"])
    if (digest(body) != ref["record_digest"] or "sha256" in ref
            and (body["sha256"], body["bytes"]) != (ref["sha256"], ref["bytes"])):
        raise EvidenceError("batch record identity mismatch")
    path = journal.artifact_path(Artifact(**body))
    if body["bytes"] > MAX_MANIFEST_BYTES:
        raise EvidenceError("batch artifact exceeds byte budget")
    raw = path.read_bytes()
    if len(raw) != body["bytes"] or hashlib.sha256(raw).hexdigest() != body["sha256"]:
        raise EvidenceError("batch artifact changed during read")
    return json.loads(raw.decode("utf-8-sig"))


def _receipt(journal, receipt, *, full_intent=True):
    if not isinstance(receipt, dict):
        receipt = _read(receipt)
    if receipt.get("schema") != RECEIPT or receipt.get("campaign") != str(journal.store.path):
        raise EvidenceError("batch receipt belongs to another schema/campaign")
    if full_intent:
        root = Path(receipt["receipt_path"]).parent
        if file_identity(root / "intent.json") != receipt["intent_identity"]:
            raise EvidenceError("batch intent identity changed")
    elif _read(receipt["receipt_path"]) != receipt:
        # A bounded read binds to the small durable receipt and exact Artifact
        # refs. It does not scan the full original manifest embedded in intent.
        raise EvidenceError("batch page receipt differs from saved immutable receipt")
    return receipt


def _index(journal, receipt):
    """Validate the entire small directory before using any claimed range."""
    index = _load(journal, receipt["index_ref"])
    role = journal.get(receipt["index_ref"]["record_id"])["role"]
    if (index.get("schema") not in {INDEX, RANGE_INDEX} or index.get("batch_id") != receipt["batch_id"]
            or index.get("task_id") != receipt["task_id"] or index.get("manifest_ref") != receipt["manifest_ref"]
            or type(index.get("expected")) is not int or not 1 <= index["expected"] <= MAX_ATTEMPTS
            or type(index.get("chunk_count")) is not int or not 1 <= index["chunk_count"] <= index["expected"]
            or not isinstance(index.get("pages"), list) or not index["pages"]):
        raise EvidenceError("batch index mismatch")
    ordinal = offset = 0
    for page in index["pages"]:
        if (type(page.get("first_chunk")) is not int or page["first_chunk"] != ordinal
                or type(page.get("chunk_count")) is not int or not 1 <= page["chunk_count"] <= INDEX_PAGE_CHUNKS):
            raise EvidenceError("batch index page order/count mismatch")
        ordinal += page["chunk_count"]
        if index["schema"] == RANGE_INDEX:
            if (type(page.get("attempt_offset")) is not int or page["attempt_offset"] != offset
                    or type(page.get("attempt_count")) is not int
                    or not page["chunk_count"] <= page["attempt_count"] <= page["chunk_count"] * CHUNK_ATTEMPTS):
                raise EvidenceError("batch index global attempt range mismatch")
            offset += page["attempt_count"]
    if ordinal != index["chunk_count"] or index["schema"] == RANGE_INDEX and offset != index["expected"]:
        raise EvidenceError("batch index omits or duplicates chunks/attempts")
    return index, role


def _content(journal, ref, role):
    body = journal.get(ref["record_id"])
    if body["role"] != role or role != "diagnostic" and body["bindings"].get("visibility") != "sealed":
        raise EvidenceError("batch index/chunk downgraded evidence role or visibility")
    return _load(journal, ref)


def _chunks(journal, receipt, *, offset=0, end=None, index_info=None):
    """Load matching chunks, validating global directory and selected page spans.

    Old indexes lack root attempt ranges. They load every small index page to
    validate their directory, while still opening only matching chunk payloads.
    Full audit requests the whole range and continues to open every chunk.
    """
    index, role = _index(journal, receipt) if index_info is None else index_info
    end = index["expected"] if end is None else end
    selected = []
    legacy_offset = 0
    for page in index["pages"]:
        ranged = index["schema"] == RANGE_INDEX
        if ranged and not (page["attempt_offset"] < end
                           and page["attempt_offset"] + page["attempt_count"] > offset):
            continue
        page_content = _content(journal, page["ref"], role)
        if (page_content.get("schema") != INDEX_PAGE or page_content.get("batch_id") != receipt["batch_id"]
                or page_content.get("task_id") != receipt["task_id"]):
            raise EvidenceError("batch index page order mismatch")
        if page["chunk_count"] != len(page_content["chunks"]) or not 1 <= len(page_content["chunks"]) <= INDEX_PAGE_CHUNKS:
            raise EvidenceError("batch index page count mismatch")
        ordinal = page["first_chunk"]
        start = page["attempt_offset"] if ranged else legacy_offset
        for item in page_content["chunks"]:
            if (type(item.get("ordinal")) is not int or item["ordinal"] != ordinal
                    or type(item.get("offset")) is not int or item["offset"] != start
                    or type(item.get("attempts")) is not int or not 1 <= item["attempts"] <= CHUNK_ATTEMPTS):
                raise EvidenceError("batch chunk directory order/count mismatch")
            if start < end and start + item["attempts"] > offset:
                selected.append(item)
            ordinal += 1
            start += item["attempts"]
        if ranged and start != page["attempt_offset"] + page["attempt_count"]:
            raise EvidenceError("batch index page attempt range mismatch")
        legacy_offset = start
    if index["schema"] == INDEX and legacy_offset != index["expected"]:
        raise EvidenceError("batch index omits or duplicates chunks/attempts")
    for item in selected:
        chunk = _content(journal, item["ref"], role)
        if (chunk.get("schema") != CHUNK or chunk.get("batch_id") != receipt["batch_id"]
                or chunk.get("task_id") != receipt["task_id"] or chunk.get("ordinal") != item["ordinal"]
                or chunk.get("offset") != item["offset"] or len(chunk["attempts"]) != item["attempts"]):
            raise EvidenceError("batch chunk order/count mismatch")
        yield chunk


def _verify_attempt(journal, attempt, receipt, default_role, verified):
    row = attempt["row"]
    specs = list(_specs(row))
    if len(specs) != len(attempt["archives"]):
        raise EvidenceError("batch original/counterevidence inventory incomplete")
    for (kind, name, spec), archive in zip(specs, attempt["archives"]):
        body = journal.get(archive["record_id"])
        if (archive["kind"] != kind or archive["name"] != name or archive["original"] != spec
                or body != archive["artifact"] or digest(body) != archive["record_digest"]
                or body["sha256"] != spec["sha256"] or body["bytes"] != spec["bytes"]
                or body["source_kind"] != receipt["source_kind"]
                or body["role"] != max((spec.get("role", default_role), default_role), key=ROLES.index)
                or body["role"] != "diagnostic" and body["bindings"].get("visibility") != "sealed"):
            raise EvidenceError("batch original record/identity mismatch")
        if archive["record_id"] not in verified:
            journal.artifact_path(Artifact(**body))
            verified.add(archive["record_id"])


def verify_batch_results(journal, receipt):
    """Recheck *all* chunk bytes, archived originals and declared coverage.

    This is an explicit archival audit. It does not reduce the original-byte or
    counterevidence obligations to a trusted summary, or rerun a scientific tool.
    """
    receipt = _receipt(journal, receipt)
    root = Path(receipt["receipt_path"]).parent
    intent = _read_json(root / "intent.json")
    manifest = validate_batch_manifest(_load(journal, receipt["manifest_ref"]))
    default_role = intent.get("default_role", "diagnostic")
    if receipt.get("default_role", default_role) != default_role:
        raise EvidenceError("batch receipt default role differs from immutable intent")
    artifact_role = _role(manifest, default_role)
    for key in ("manifest_ref", "index_ref", "summary_ref", "step_ref"):
        body = journal.get(receipt[key]["record_id"])
        if body["role"] != artifact_role or (key != "step_ref" and artifact_role != "diagnostic"
                and body["bindings"].get("visibility") != "sealed"):
            raise EvidenceError("batch derivative downgraded evidence role or visibility")
    if (intent["manifest"] != manifest or intent["source_id"] != receipt["source_id"]
            or intent["campaign"] != receipt["campaign"] or intent["source_kind"] != receipt["source_kind"]
            or manifest["batch_id"] != receipt["batch_id"] or manifest["task_id"] != receipt["task_id"]):
        raise EvidenceError("batch receipt differs from immutable intent")
    rows = {_key(row): row for row in manifest["rows"]}
    observed, verified = [], set()
    for chunk in _chunks(journal, receipt):
        for attempt in chunk["attempts"]:
            expected = manifest["expected_attempts"][len(observed)]
            row = rows.get(_key(expected))
            if attempt["expected"] != expected or attempt["row"] != row:
                raise EvidenceError("chunk differs from the frozen full inventory")
            _verify_attempt(journal, attempt, receipt, default_role, verified)
            observed.append(expected)
    if observed != manifest["expected_attempts"]:
        raise EvidenceError("batch expected inventory incomplete")
    summary = _load(journal, receipt["summary_ref"])
    if summary != {**_summary(manifest), "index_ref": receipt["index_ref"], "manifest_ref": receipt["manifest_ref"]}:
        raise EvidenceError("batch summary differs from recomputed complete inventory")
    step = _load(journal, receipt["step_ref"])
    if (step.get("task_id") != manifest["task_id"] or step.get("branch_id") != manifest["branch_id"]
            or step.get("output_refs") != [receipt["index_ref"], receipt["summary_ref"]]
            or step.get("input_refs") != [receipt["manifest_ref"]]
            or step.get("detail", {}).get("receipt_refs") != [receipt["summary_ref"]]):
        raise EvidenceError("batch research-step linkage mismatch")
    return {"schema": "etalon-batch-results-audit/1", "batch_id": manifest["batch_id"],
        "status": "verified", "originals_verified": len(verified), "expected_verified": len(observed),
        "summary": summary, "scientific_truth": "not_certified", "automatic_retry": False}


def read_batch_results(journal, receipt, *, offset=0, limit=32, cursor=None):
    """Bounded row navigation; verifies returned originals, never claims full audit.

    Use the existing evidence/read_scope API for published-source visibility and
    byte pagination. This local helper is for the campaign owner with its journal.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= CHUNK_ATTEMPTS:
        raise EvidenceError("batch page requires nonnegative offset and limit 1..32")
    receipt = _receipt(journal, receipt, full_intent=False)
    version = digest({k: receipt[k] for k in ("campaign", "batch_id", "intent_identity", "index_ref", "manifest_ref")})
    if cursor is not None:
        _fields(cursor, ("schema", "batch_version", "offset"))
        if (cursor["schema"] != "etalon-batch-results-cursor/1" or cursor["batch_version"] != version
                or type(cursor["offset"]) is not int or cursor["offset"] < 0 or offset != 0):
            raise EvidenceError("batch cursor version/offset mismatch")
        offset = cursor["offset"]
    index_info = _index(journal, receipt)
    total = index_info[0]["expected"]
    default_role = receipt.get("default_role")
    if default_role is None:  # Old role-bearing receipts remain readable.
        default_role = _read_json(Path(receipt["receipt_path"]).parent / "intent.json").get("default_role", "diagnostic")
    result, verified = [], set()
    for chunk in _chunks(journal, receipt, offset=offset, end=offset + limit, index_info=index_info):
        for position, attempt in enumerate(chunk["attempts"], chunk["offset"]):
            if offset <= position < offset + limit:
                # Validate the selected row's declared inventory too; never infer
                # complete counterevidence from the number of archives alone.
                validate_batch_manifest({"schema": INPUT, "batch_id": receipt["batch_id"],
                    "task_id": receipt["task_id"], "question": "Bounded original verification", "branch_id": "read",
                    "expected_attempts": [attempt["expected"]], "rows": [] if attempt["row"] is None else [attempt["row"]]})
                _verify_attempt(journal, attempt, receipt, default_role, verified)
                result.append(attempt)
    if len(result) != max(0, min(limit, total - offset)):
        raise EvidenceError("batch page requested range incomplete")
    if result:
        local_summary = _summary({"batch_id": receipt["batch_id"], "task_id": receipt["task_id"],
            "branch_id": "bounded-read", "expected_attempts": [a["expected"] for a in result],
            "rows": [a["row"] for a in result if a["row"] is not None]})
        range_coverage = {key: local_summary[key] for key in ("expected", "reported", "missing",
            "required_originals", "required_counterevidence", "missing_required_originals",
            "missing_required_counterevidence", "coverage")}
    else:
        range_coverage = {"expected": 0, "reported": 0, "missing": 0,
            "required_originals": 0, "required_counterevidence": 0,
            "missing_required_originals": 0, "missing_required_counterevidence": 0, "coverage": "empty_range"}
    next_offset = offset + len(result) if offset + len(result) < total else None
    return {"schema": "etalon-batch-results-page/1", "batch_id": receipt["batch_id"],
        "batch_version": version,
        "offset": offset, "limit": limit, "total": total, "attempts": result,
        "range_coverage": range_coverage,
        "next_offset": next_offset, "next_cursor": None if next_offset is None else {
            "schema": "etalon-batch-results-cursor/1", "batch_version": version, "offset": next_offset},
        "verification_scope": "saved receipt, global index directory, selected page/chunk bytes and returned originals/counterevidence; full intent/manifest and unselected evidence require full audit"}
