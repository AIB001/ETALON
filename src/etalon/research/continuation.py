"""Optional research steps and read-only continuation over the existing journal.

These are ordinary immutable JSON Artifacts, not another ledger or scheduler.
Status guards are host interpretations bound to exact authority-file bytes;
changed/absent authority requires review. Resuming NEVER grants execution or
submits, retries, reconciles, charges, selects a method, or changes a branch.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from etalon.active.schema import canonical, digest
from .evidence_refs import (
    EvidenceError, EvidenceResolver, _identity, _local, _sha, _text,
    file_identity, load_workspace, make_ref, validate_claim, validate_ref,
)
from .schema import Artifact

STEP = "etalon-research-step/1"
CHECKPOINT = "etalon-research-checkpoint/1"
MAX_CONTENT_BYTES = 16384
MAX_REFS = 64
MAX_RESUME_RECORDS = 128


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise EvidenceError("missing or unsupported research content fields")


def _list(value, *, nonempty=False, limit=MAX_REFS):
    if not isinstance(value, list) or len(value) > limit or (nonempty and not value):
        raise EvidenceError("expected a bounded explicit list")
    return value


def _refs(value, *, nonempty=False):
    for ref in _list(value, nonempty=nonempty):
        validate_ref(ref)


def _watermarks(value):
    seen = set()
    for item in _list(value):
        _fields(item, ("source_id", "snapshot", "read_watermark"))
        sid = _text(item["source_id"])
        _sha(item["snapshot"])
        mark = item["read_watermark"]
        if sid in seen or (mark is not None and (type(mark) is not int or mark < 0)):
            raise EvidenceError("invalid or duplicate source watermark")
        seen.add(sid)


def _handle(value):
    _fields(value, ("schema", "backend", "workspace", "request_id", "job_id", "plan_id", "node_id"), ("state",))
    if (value["schema"], value["backend"], value["node_id"]) != ("etalon-action-handle/1", "runtime", "action"):
        raise EvidenceError("unsupported pending action handle")
    for key in ("workspace", "request_id", "job_id", "plan_id"):
        _text(value[key])
    if not Path(value["workspace"]).is_absolute():
        raise EvidenceError("action workspace must be an absolute native path")


def validate_content(payload):
    """Validate shape/meaning of the record, never scientific truth or adoption."""
    if not isinstance(payload, dict):
        raise EvidenceError("research content must be an object")
    if len(canonical(payload).encode()) > MAX_CONTENT_BYTES:
        raise EvidenceError("research content exceeds 16 KiB; link separate artifacts")
    schema = payload.get("schema")
    from .method_contract import SCHEMAS as CONTRACT_SCHEMAS
    if schema in CONTRACT_SCHEMAS:
        from .method_contract import validate_content as validate_contract_content
        return validate_contract_content(payload)
    if schema == "etalon-research-trace/1":
        from .research_trace import validate_content as validate_trace
        return validate_trace(payload)
    from .improvement import SCHEMAS as IMPROVEMENT_SCHEMAS
    if schema in IMPROVEMENT_SCHEMAS:
        from .improvement import validate_content as validate_improvement
        return validate_improvement(payload)
    if schema == "etalon-research-claim/1":
        return validate_claim(payload)
    if schema == "etalon-research-workflow/1":
        from .composition import validate_workflow
        return validate_workflow(payload)
    if schema == STEP:
        _fields(payload, ("schema", "task_id", "question", "branch_id", "operation", "reason",
                         "input_refs", "output_refs", "method_refs", "accounting_refs",
                         "source_watermarks", "supersedes", "detail"))
        for key in ("task_id", "question", "branch_id", "reason"):
            _text(payload[key])
        for key in ("input_refs", "output_refs", "method_refs", "accounting_refs", "supersedes"):
            _refs(payload[key])
        _watermarks(payload["source_watermarks"])
        detail = payload["detail"]
        operation = payload["operation"]
        if operation == "search":
            _fields(detail, ("query", "source_ids", "conditions", "coverage", "selected_refs", "excluded", "refresh_when"))
            if not isinstance(detail["query"], str):
                raise EvidenceError("query must be explicit text")
            for sid in _list(detail["source_ids"], nonempty=True):
                _text(sid)
            _text(detail["conditions"])
            _text(detail["refresh_when"])
            for cov in _list(detail["coverage"], nonempty=True):
                _fields(cov, ("source_id", "status", "truncated", "errors"))
                _text(cov["source_id"])
                if cov["status"] not in {"complete", "partial", "unavailable"} or type(cov["truncated"]) is not bool:
                    raise EvidenceError("invalid search coverage")
                for error in _list(cov["errors"]):
                    _text(error)
                if cov["status"] == "complete" and (cov["truncated"] or cov["errors"]):
                    raise EvidenceError("failed/truncated search cannot claim complete coverage")
            if not set(detail["source_ids"]) <= {c["source_id"] for c in detail["coverage"]}:
                raise EvidenceError("search coverage must account for every requested source")
            _refs(detail["selected_refs"])
            for excluded in _list(detail["excluded"]):
                _fields(excluded, ("ref", "reason"))
                validate_ref(excluded["ref"])
                _text(excluded["reason"])
        elif operation == "judgment":
            _fields(detail, ("claim_refs",))
            _refs(detail["claim_refs"], nonempty=True)
        elif operation == "plan":
            _fields(detail, ("hypothesis_refs", "prediction", "controls", "readouts", "protocol_refs", "decision_rule", "stop_rule"))
            _refs(detail["hypothesis_refs"], nonempty=True)
            _refs(detail["protocol_refs"], nonempty=True)
            for key in ("prediction", "decision_rule", "stop_rule"):
                _text(detail[key])
            for control in _list(detail["controls"], nonempty=True):
                _text(control)
            names = []
            for readout in _list(detail["readouts"], nonempty=True):
                _fields(readout, ("name", "unit"))
                names.append(_text(readout["name"]))
                _text(readout["unit"])
            if len(set(names)) != len(names):
                raise EvidenceError("plan readouts must be distinct")
        elif operation == "result":
            _fields(detail, ("experiment_refs", "observation_refs", "action_handles"))
            _refs(detail["experiment_refs"], nonempty=True)
            _refs(detail["observation_refs"], nonempty=True)
            for handle in _list(detail["action_handles"]):
                _handle(handle)
        elif operation == "decision":
            _fields(detail, ("choice", "alternatives", "basis_refs", "reconsider_when"))
            if detail["choice"] not in {"reuse", "proceed", "fork", "merge", "defer", "stop", "reconsider"}:
                raise EvidenceError("unknown branch choice")
            _refs(detail["basis_refs"], nonempty=True)
            _text(detail["reconsider_when"])
            for alternative in _list(detail["alternatives"]):
                _fields(alternative, ("branch_id", "disposition", "reason"))
                for text in alternative.values():
                    _text(text)
        elif operation in {"node", "evaluation_plan", "evaluation"}:
            from .composition import validate_step_extension
            validate_step_extension(payload)
        else:
            raise EvidenceError("unknown research step operation")
    elif schema == CHECKPOINT:
        _fields(payload, ("schema", "task_id", "question", "step_refs", "claim_refs", "counter_refs",
                         "method_refs", "branches", "pending_handles", "source_watermarks", "status_guards", "supersedes"))
        _text(payload["task_id"])
        _text(payload["question"])
        for key in ("step_refs", "claim_refs", "counter_refs", "method_refs", "supersedes"):
            _refs(payload[key], nonempty=key == "step_refs")
        _watermarks(payload["source_watermarks"])
        ids = set()
        for branch in _list(payload["branches"], nonempty=True):
            _fields(branch, ("branch_id", "state", "basis_refs", "next_read_refs", "reconsider_when"))
            bid = _text(branch["branch_id"])
            if bid in ids or branch["state"] not in {"open", "active", "waiting", "resolved", "stopped"}:
                raise EvidenceError("invalid or duplicate research branch")
            ids.add(bid)
            _refs(branch["basis_refs"], nonempty=True)
            _refs(branch["next_read_refs"])
            _text(branch["reconsider_when"])
        for handle in _list(payload["pending_handles"]):
            _handle(handle)
        locations = set()
        for guard in _list(payload["status_guards"]):
            _fields(guard, ("location", "sha256", "bytes", "state"))
            location = _text(guard["location"])
            _identity(guard)
            if location in locations or guard["state"] not in {"active", "paused", "stopped", "unknown"}:
                raise EvidenceError("invalid or duplicate status guard")
            locations.add(location)
    else:
        raise EvidenceError("unsupported research content schema")
    return json.loads(canonical(payload))


def _exact_refs(value):
    if isinstance(value, dict):
        if value.get("schema") == "etalon-evidence-ref/1":
            yield validate_ref(value)
        else:
            for item in value.values():
                yield from _exact_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _exact_refs(item)


def record_content(journal, payload, directory, *, source_id, source_kind, role="diagnostic"):
    """Save claim/step/checkpoint through ResearchJournal.record(Artifact).

    Local references and original files are verified; external references retain
    explicit unresolved provenance until read from a published workspace. A failed
    DB append leaves an intact, content-addressed orphan; retry is idempotent.
    """
    content = validate_content(payload)
    _text(source_id)
    external = False
    local_records = {}
    for ref in _exact_refs(content):
        if ref["source_id"] != source_id:
            external = True
            continue
        body = journal.get(ref["record_id"])
        if digest(body) != ref["record_digest"]:
            raise EvidenceError("local record digest mismatch")
        if ref["record_id"].startswith("artifact-"):
            journal.artifact_path(Artifact(**body))
            if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (body["sha256"], body["bytes"]):
                raise EvidenceError("local artifact identity mismatch")
        if "member" in ref:
            raise EvidenceError("local member refs require an explicitly published source envelope")
        local_records[ref["record_id"]] = body
    typed_refs = []
    from .improvement import SCHEMAS as IMPROVEMENT_SCHEMAS
    if content["schema"] in IMPROVEMENT_SCHEMAS:
        from .improvement import validate_local_links as validate_improvement_links
        validate_improvement_links(journal, content, source_id)
    if content["schema"] == "etalon-research-trace/1":
        from .research_trace import validate_local_links as validate_trace_links
        validate_trace_links(journal, content, source_id)
    if content["schema"] == CHECKPOINT:
        typed_refs.extend((r, STEP) for r in content["step_refs"])
        typed_refs.extend((r, "etalon-research-claim/1") for r in content["claim_refs"])
    elif content["schema"] == STEP and content["operation"] == "judgment":
        typed_refs.extend((r, "etalon-research-claim/1") for r in content["detail"]["claim_refs"])
    typed_refs.extend((r, content["schema"]) for r in content["supersedes"])
    for target, expected in typed_refs:
        if target["source_id"] == source_id:
            body = local_records[target["record_id"]]
            if body.get("bindings", {}).get("schema") != expected:
                raise EvidenceError("referenced research content has the wrong schema")
    if content["schema"] == "etalon-research-workflow/1" or (
            content["schema"] == STEP and content["operation"] in {"node", "evaluation_plan", "evaluation"}):
        from .composition import validate_local_links
        validate_local_links(journal, content, source_id, local_records)
    if content["schema"] == STEP and content["operation"] == "result":
        detail = content["detail"]
        experiments = {r["record_id"] for r in detail["experiment_refs"] if r["source_id"] == source_id}
        for ref in detail["experiment_refs"]:
            if ref["source_id"] == source_id and not ref["record_id"].startswith("experiment-"):
                raise EvidenceError("result requires existing Experiment references")
        for ref in detail["observation_refs"]:
            if ref["source_id"] == source_id and (not ref["record_id"].startswith("observation-")
                    or local_records[ref["record_id"]].get("experiment_id") not in experiments):
                raise EvidenceError("result Observation must belong to the referenced Experiment")
        handles = detail["action_handles"]
    elif content["schema"] == STEP and content["operation"] == "node":
        handles = content["detail"]["action_handles"]
    else:
        handles = content.get("pending_handles", [])
    if handles:
        from etalon.runtime.api import read_action_result
        for handle in handles:
            read_action_result(handle)  # Existing handle only, never submit/reconcile.
    data = (canonical(content) + "\n").encode()
    directory = Path(directory).absolute()
    path = directory / (digest(content) + ".json")
    artifact = Artifact(str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                        source_kind=source_kind, role=role, bindings={"schema": content["schema"],
                        "source_id": source_id, "task_id": content.get("task_id", ""),
                        "reference_verification": "local_checked_external_unresolved" if external else "local_checked"})
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".research-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise EvidenceError("immutable research content conflicts") from None
    finally:
        temporary.unlink()
    if file_identity(path) != {"sha256": artifact.sha256, "bytes": artifact.bytes}:
        raise EvidenceError("published research content changed")
    return journal.record(artifact)


def capture_watermarks(workspace, *, source_ids):
    """Capture only complete, explicitly selected sources and their dependencies.

    Capture stable evidence inputs, not the journal about to receive this record.
    A checkpoint cannot include its own post-append digest. Its explicit record
    references and authority guards are checked separately at continuation time.
    """
    from .evidence import _collect
    ws = load_workspace(workspace)
    for sid in _list(source_ids, nonempty=True):
        _text(sid)
    _, coverage, _ = _collect(ws, source_ids)
    if any(c["status"] != "complete" for c in coverage):
        raise EvidenceError("cannot capture complete watermarks from partial/unavailable sources")
    return [{key: c[key] for key in ("source_id", "snapshot", "read_watermark")} for c in coverage]


def read_content(workspace, ref):
    """Read a typed claim/step/checkpoint/workflow Artifact, verifying its bytes.

    Ordinary component JSON/results are read_evidence(view="artifact") inputs;
    their application schemas are deliberately not accepted by this typed reader.
    """
    from .evidence import read_evidence
    result = read_evidence(workspace, ref, view="artifact", limit=MAX_CONTENT_BYTES + 1)
    return _content_result(result)


def _content_result(result):
    fragment = result.get("fragment", {})
    if (result.get("availability") != "available" or result.get("verification", {}).get("artifact") != "verified"
            or fragment.get("next_offset") is not None or fragment.get("total_bytes", MAX_CONTENT_BYTES + 2) > MAX_CONTENT_BYTES + 1):
        raise EvidenceError("research content unavailable, unverified or oversized")
    return validate_content(json.loads(base64.b64decode(fragment["content"]).decode("utf-8-sig")))


def _status(ws, guards, *, source_id):
    """Never infer an executable status from prose or a stale checkpoint."""
    results = []
    authority = ws.manifest.get("status_refs", [])
    locations = set()
    unsupported = False
    for item in authority:
        location = item if isinstance(item, str) else item.get("location") if isinstance(item, dict) else None
        if not isinstance(location, str) or not location:
            unsupported = True
        else:
            locations.add(location)
    guarded = {g["location"]: g for g in guards}
    for location in sorted(locations | set(guarded)):
        guard = guarded.get(location)
        row = {"location": location, "state": "unknown", "status": "review_required"}
        try:
            mapped = any(entry["source_id"] == source_id and entry["original"] == location
                         for entry in ws.manifest.get("location_map", []))
            if mapped and guard:
                path = EvidenceResolver(ws, source_id).resolve(
                    location, sha256=guard["sha256"], bytes=guard["bytes"])
                row["resolved_location"] = str(path)
            else:
                path = _local(ws.base, location)
            identity = file_identity(path)
            row["observed_identity"] = identity
            if location in locations and guard and identity == {k: guard[k] for k in ("sha256", "bytes")}:
                row.update(state=guard["state"], status="unchanged", basis="host_interpretation_bound_to_current_bytes")
            else:
                row["reason"] = "authority_changed_or_unreviewed"
            # A short current excerpt helps the host review a changed authority.
            with path.open("rb") as handle:
                row["current_excerpt"] = handle.read(2048).decode("utf-8", errors="replace")
            if file_identity(path) != identity:
                row.update(state="unknown", status="review_required", reason="authority_changed_during_read")
        except (OSError, ValueError):
            row.update(reason="authority_unavailable")
        results.append(row)
    states = {r["state"] for r in results}
    if "stopped" in states:
        state = "stopped"
    elif "paused" in states:
        state = "paused"
    elif unsupported or not locations or any(r["status"] != "unchanged" or r["state"] != "active" for r in results):
        state = "review_required"
    else:
        state = "active_at_read"
    return {"state": state, "sources": results, "unsupported_status_refs": unsupported}


def _check_frozen_view(ws, records, coverage):
    """Recheck source and projected bytes before returning a reused frozen view."""
    for cov in coverage:
        source = ws.source(cov["source_id"])
        path = _local(ws.base, source["location"])
        if file_identity(path)["sha256"] != source["identity"]["value"]:
            raise EvidenceError("frozen source changed")
        wal = Path(str(path) + "-wal")
        if source["kind"] == "journal" and wal.exists() and wal.stat().st_size:
            raise EvidenceError("frozen journal acquired WAL content")
        for row in records.get(cov["source_id"], {}).values():
            if "projection" in row:
                body = row["record"]
                EvidenceResolver(ws, cov["source_id"]).resolve(body["path"], sha256=body["sha256"], bytes=body["bytes"])


def resume_research(workspace, checkpoint_ref, *, read_mode="fresh"):
    """Read checked context. frozen_view optionally reuses one closed snapshot view.

    The default remains the existing fresh-read method. Reuse never spans calls;
    live, partial or non-closed source sets fall back to fresh reads. Neither mode
    grants execution or changes a method default.
    """
    from .evidence import _collect, _relationships, _read_evidence
    if read_mode not in {"fresh", "frozen_view"}:
        raise EvidenceError("unknown continuation read mode")
    ws = load_workspace(workspace)
    checkpoint_ref = validate_ref(checkpoint_ref)
    collection = None
    if read_mode == "frozen_view":
        collection = _collect(ws, [checkpoint_ref["source_id"]])
        content = _content_result(_read_evidence(ws, checkpoint_ref, view="artifact",
            limit=MAX_CONTENT_BYTES + 1, collection=collection))
    else:
        content = read_content(ws, checkpoint_ref)
    if content["schema"] != CHECKPOINT:
        raise EvidenceError("continuation requires a research checkpoint")
    selected = list(dict.fromkeys([checkpoint_ref["source_id"],
                                  *(w["source_id"] for w in content["source_watermarks"])]))
    reusable = (collection is not None and not collection[2]
        and set(selected) == {c["source_id"] for c in collection[1]}
        and all(c["status"] == "complete" and ws.source(c["source_id"])["identity"]["kind"] == "snapshot_sha256"
                for c in collection[1]))
    if reusable:
        # All visited sources are requested; preserve the ordinary collector's
        # manifest order and scope semantics without rescanning their contents.
        records, coverage, warnings = collection
        ordered = [s["source_id"] for s in ws.manifest["sources"] if s["source_id"] in selected]
        records = {sid: records[sid] for sid in ordered}
        indexed = {c["source_id"]: c for c in coverage}
        coverage = [{**indexed[sid], "scope": "requested"} for sid in ordered]
        collection = records, coverage, warnings
    else:
        records, coverage, warnings = _collect(ws, selected)
        collection = None
    current = {c["source_id"]: c for c in coverage}
    changes = []
    for old in content["source_watermarks"]:
        now = current.get(old["source_id"], {})
        unchanged = now.get("status") == "complete" and all(now.get(k) == old[k] for k in ("snapshot", "read_watermark"))
        changes.append({"source_id": old["source_id"], "status": "unchanged" if unchanged else "review_required",
                        "expected": old, "observed": now})
    pending_refs = list(_exact_refs(content))
    checked, seen, issues = [], set(), []
    checkpoint_row = records.get(checkpoint_ref["source_id"], {}).get(checkpoint_ref["record_id"])
    if checkpoint_row is None or digest(checkpoint_row["record"]) != checkpoint_ref["record_digest"]:
        issues.append({"reason": "checkpoint_changed_or_unpublished_during_read"})
    for ref in pending_refs:
        key = canonical(ref)
        if key in seen:
            continue
        if len(seen) >= MAX_RESUME_RECORDS:
            issues.append({"reason": "reference_walk_truncated", "limit": MAX_RESUME_RECORDS})
            break
        seen.add(key)
        row = records.get(ref["source_id"], {}).get(ref["record_id"])
        reason = None
        if row is None:
            reason = "unavailable"
        elif digest(row["record"]) != ref["record_digest"]:
            reason = "digest_mismatch"
        elif row.get("projection", {}).get("status") == "unavailable":
            reason = "content_projection_unavailable"
        elif "member" in ref:
            # Use existing member/identity rules rather than another resolver.
            from .evidence import read_evidence
            try:
                result = (_read_evidence(ws, ref, view="artifact", limit=1, collection=collection)
                          if collection is not None else read_evidence(ws, ref, view="artifact", limit=1))
                if result.get("verification", {}).get("artifact") != "verified":
                    reason = "member_unavailable"
            except (OSError, ValueError):
                reason = "member_identity_mismatch"
        elif row["record_type"] == "artifact":
            body = row["record"]
            try:
                if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (body["sha256"], body["bytes"]):
                    raise EvidenceError("artifact identity mismatch")
                EvidenceResolver(ws, ref["source_id"]).resolve(body["path"], sha256=body["sha256"], bytes=body["bytes"])
            except (OSError, ValueError):
                reason = "artifact_unavailable_or_changed"
        if reason:
            issues.append({"ref": ref, "reason": reason})
            continue
        checked.append(ref)
        for target in _relationships(row):
            if isinstance(target, dict):
                pending_refs.append(validate_ref(target))
            elif isinstance(target, str):
                linked = records.get(ref["source_id"], {}).get(target)
                if linked:
                    pending_refs.append(make_ref(ref["source_id"], target, linked["record"]))
                else:
                    issues.append({"source_id": ref["source_id"], "record_id": target, "reason": "legacy_reference_unavailable"})
    # Observe successors in the currently published view; never silently replace
    # the caller's selected checkpoint or resolve conflicting scientific claims.
    ancestors = set()
    ancestor_refs = list(content["supersedes"])
    for ancestor in ancestor_refs:
        identity = (ancestor["source_id"], ancestor["record_id"])
        if identity in ancestors:
            continue
        if len(ancestors) >= MAX_RESUME_RECORDS:
            issues.append({"reason": "checkpoint_history_truncated"})
            break
        ancestors.add(identity)
        prior = records.get(identity[0], {}).get(identity[1], {})
        if digest(prior.get("record", {})) != ancestor["record_digest"]:
            issues.append({"reason": "checkpoint_ancestor_mismatch", "ref": ancestor})
            continue
        prior_content = prior.get("projected_content", {})
        if prior_content.get("schema") == CHECKPOINT and prior_content.get("task_id") == content["task_id"]:
            ancestor_refs.extend(prior_content.get("supersedes", []))
    checkpoints = []
    for sid, rows in records.items():
        for rid, row in rows.items():
            candidate = row.get("projected_content", {})
            if (candidate.get("schema") == CHECKPOINT and candidate.get("task_id") == content["task_id"]
                    and (sid, rid) not in ancestors
                    and (sid, rid) != (checkpoint_ref["source_id"], checkpoint_ref["record_id"])):
                checkpoints.append({"source_id": sid, "record_id": rid})
            candidate_ref = make_ref(sid, rid, row["record"])
            is_selected = (sid, rid) == (checkpoint_ref["source_id"], checkpoint_ref["record_id"])
            if (candidate.get("schema") == STEP and candidate.get("task_id") == content["task_id"]
                    and canonical(candidate_ref) not in seen):
                issues.append({"reason": "uncheckpointed_task_step_requires_review", "ref": candidate_ref})
            if (canonical(candidate_ref) not in seen and not is_selected and candidate
                    and any(canonical(r) in seen for r in candidate.get("supersedes", []))):
                issues.append({"reason": "referenced_record_superseded_requires_review", "ref": candidate_ref})
    if checkpoints:
        issues.append({"reason": "other_task_checkpoints_require_review", "records": checkpoints[:MAX_REFS]})
    pending = []
    observed_handles = set()
    if content["pending_handles"]:
        from etalon.runtime.api import read_action_result
        for handle in content["pending_handles"]:
            key = canonical({k: v for k, v in handle.items() if k != "state"})
            if key in observed_handles:
                continue
            observed_handles.add(key)
            try:
                result = read_action_result(handle)
                pending.append({"handle": handle, "execution_state": result["execution_state"],
                                "verification": result["verification"], "recovery": result["recovery"],
                                "accounting_refs": result["accounting_refs"]})
            except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
                pending.append({"handle": handle, "execution_state": "unresolved", "reason": type(exc).__name__})
                issues.append({"reason": "pending_handle_unavailable", "request_id": handle["request_id"]})
    if collection is not None:
        try:
            _check_frozen_view(ws, records, coverage)
        except (OSError, ValueError):
            issues.append({"reason": "frozen_view_changed_during_read"})
    status = _status(ws, content["status_guards"], source_id=checkpoint_ref["source_id"])
    review = bool(issues or warnings or any(c["status"] != "complete" for c in coverage)
                  or any(c["status"] != "unchanged" for c in changes) or status["state"] != "active_at_read")
    return {"schema": "etalon-research-resume/1", "checkpoint_ref": checkpoint_ref,
            "task_id": content["task_id"], "question": content["question"],
            "branches": content["branches"], "step_refs": content["step_refs"],
            "claim_refs": content["claim_refs"], "counter_refs": content["counter_refs"],
            "method_refs": content["method_refs"], "source_changes": changes, "coverage": coverage,
            "checked_refs": checked, "issues": issues, "warnings": warnings, "pending": pending,
            "authority": status, "review_required": review, "execution_permission": "not_granted",
            "automatic_retry": False, "snapshot_consistency": "per_source_read_not_global_atomic_snapshot"}
