"""Optional producer-to-publication adapter for a useful segment of research.

A work object is an ordinary Artifact, not a new journal record or executable
language. The caller owns scientific choices and the producer's internal batch.
Only that Python producer executes work; publication uses the existing API.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import time

from etalon.active.schema import canonical
from etalon.host import _consumer_lock
from .continuation import read_content
from .evidence import read_evidence
from .evidence_refs import EvidenceError, file_identity
from .publication import prepare_result_publication, resume_publication
from .research_context import load_entry

WORK = "etalon-research-work/1"
RESULT = "etalon-producer-result/1"
MAX_BYTES = 65536


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _save(path, value):
    """Immutable small local output, including after controller reentry."""
    path = Path(path)
    data = (canonical(value) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != data:
            raise EvidenceError("saved research segment content changed: " + str(path))
        return
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _compact(value):
    if len(canonical(value).encode("utf-8")) > MAX_BYTES:
        raise EvidenceError("compact research object exceeds 64 KiB; retain arrays in originals")
    return value


def _texts(values):
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise EvidenceError("residuals must be explicit nonempty strings")
    return values


def _command(command, *, verify_inputs=True):
    if command is None:
        return
    if (not isinstance(command, dict) or set(command) != {"argv", "cwd", "reason", "ends_when", "inputs"}
            or not isinstance(command["argv"], list) or not command["argv"]
            or any(not isinstance(v, str) or not v for v in command["argv"])
            or not Path(command["argv"][0]).is_absolute()
            or not Path(command["cwd"]).is_absolute()
            or any(not isinstance(command[k], str) or not command[k].strip() for k in ("reason", "ends_when"))
            or not isinstance(command["inputs"], list)):
        raise EvidenceError("next segment requires an ordinary absolute argv/cwd, reason, ending and pinned inputs")
    for item in command["inputs"]:
        if not isinstance(item, dict) or set(item) != {"path", "identity"} or not Path(item["path"]).is_absolute():
            raise EvidenceError("next segment inputs require absolute path and identity")
        if verify_inputs and file_identity(item["path"]) != item["identity"]:
            raise EvidenceError("next segment input changed")


def _feedback(feedback, next_segment, *, check_choice=True):
    """Keep domain feedback open; check only an explicitly chosen runnable option.

    Producers can supply contrasts, conditions, counter_cases, untested cases,
    and any task-specific scope/fidelity/fallback metadata. No field here makes
    an unrun comparison measured or a proposed replacement validated. Optional
    actions have an id and ordinary command (null for retain/no execution).
    The producer/host selects one; this adapter never ranks or executes them.
    """
    if feedback is None:
        return
    if not isinstance(feedback, dict):
        raise EvidenceError("producer feedback must be a compact object")
    actions = feedback.get("actions", [])
    if not isinstance(actions, list):
        raise EvidenceError("feedback actions must be a list")
    by_id = {}
    for action in actions:
        if (not isinstance(action, dict) or not isinstance(action.get("id"), str)
                or not action["id"].strip() or "command" not in action):
            raise EvidenceError("feedback action requires an id and command (null for no execution)")
        if action["id"] in by_id:
            raise EvidenceError("feedback action ids must be unique")
        # Unchosen proposals can describe unavailable or untested branches.
        # Only the actual next_segment requires live dependency verification.
        _command(action["command"], verify_inputs=False)
        by_id[action["id"]] = action
    selected = feedback.get("selected_action")
    if selected is not None:
        if not isinstance(selected, str) or selected not in by_id:
            raise EvidenceError("selected feedback action must identify an offered action")
        if check_choice and by_id[selected]["command"] != next_segment:
            raise EvidenceError("selected feedback action must match next_segment")


def validate_work(work):
    if not isinstance(work, dict) or work.get("schema") != WORK:
        raise EvidenceError("research work object required")
    for key in ("task_id", "branch_id", "goal", "question", "change_when", "source_entry"):
        if not isinstance(work.get(key), str) or not work[key].strip():
            raise EvidenceError("research work requires " + key)
    _texts(work.get("residuals"))
    if not isinstance(work.get("completed_refs"), list):
        raise EvidenceError("completed references must be a list")
    _command(work.get("next_segment"))
    # A host may replace a note's next_action after reading producer feedback.
    # That current choice wins; the producer's reported choice stays intact.
    # Producer results themselves always require a consistent selected action.
    _feedback(work.get("feedback"), work.get("next_segment"), check_choice="source_record_id" not in work)
    return _compact(work)


def _note_choice(work):
    """Label a later host override without changing the historical feedback."""
    feedback = work.get("feedback") or {}
    selected = feedback.get("selected_action")
    work.pop("feedback_selection_status", None)
    if selected is not None:
        chosen = next(action for action in feedback["actions"] if action["id"] == selected)
        work["feedback_selection_status"] = (
            "current" if chosen["command"] == work["next_segment"] else "superseded_by_host")
    return _compact(work)


def work_from_entry(goal, entry, *, branch_id=None, residuals=None, next_segment=None,
                    change_when="New evidence changes the goal, interpretation or next useful action"):
    """Construct compact runnable context from natural text and an existing entry.

    A branch is inferred only when exactly one exists. No scientific action or
    authority is inferred from a goal. Pauses remain on the original checkpoint.
    """
    entry = Path(entry).absolute()
    from .notes import ENTRY, entry_value, read_entry
    if entry_value(entry).get("schema") == ENTRY:
        current = read_entry(entry, branch_id=branch_id, verify_originals=False)
        note = current["note"]
        if next_segment is None and isinstance(note.get("next_action"), dict) and "argv" in note["next_action"]:
            next_segment = note["next_action"]
        work = {"schema": WORK, "task_id": note["task_id"], "branch_id": note["branch_id"],
            "goal": goal, "question": note["question"], "change_when": change_when,
            "source_entry": str(entry), "source_record_id": current["record_id"],
            "last_summary": note["summary"], "next_action": note.get("next_action"),
            "branch_state": note.get("state", note.get("status", "unspecified")),
            "completed_refs": [{"entry": str(entry), "record_id": current["record_id"]}],
            "residuals": list(residuals) if residuals is not None else note.get("residuals", [note["question"]]),
            "next_segment": next_segment, "status_guards": note.get("status_guards", []),
            "pending_handles": note.get("pending_handles", []), "execution_permission": "not_granted"}
        # Preserve current task constraints without copying a note's full memory.
        for key in ("conditions", "authorization", "constraints", "controls", "budget", "other_pauses", "state", "status"):
            if key in note:
                work[key] = note[key]
        if note.get("feedback") is not None:
            work["feedback"] = note["feedback"]
        return _note_choice(validate_work(work))
    workspace, ref = load_entry(entry)
    checkpoint = read_content(workspace, ref)
    branches = checkpoint["branches"]
    if branch_id is None and len(branches) == 1:
        branch_id = branches[0]["branch_id"]
    branch = next((b for b in branches if b["branch_id"] == branch_id), None)
    if branch is None:
        raise EvidenceError("select an existing branch for the work object")
    return validate_work({"schema": WORK, "task_id": checkpoint["task_id"], "branch_id": branch_id,
        "goal": goal, "question": checkpoint["question"], "change_when": change_when,
        "source_entry": str(entry), "source_checkpoint_ref": ref,
        "branch_state": branch["state"], "completed_refs": branch["basis_refs"],
        "residuals": list(residuals) if residuals is not None else [checkpoint["question"]],
        "next_segment": next_segment, "status_guards": checkpoint["status_guards"],
        "pending_handles": checkpoint["pending_handles"], "execution_permission": "not_granted"})


def result_file(path, name, *, source_kind="existing_real_artifact", role="diagnostic", bindings=None):
    """Bind real producer bytes without fabricating typed scientific observations."""
    path = Path(path).absolute()
    return {"name": name, "path": str(path), "identity": file_identity(path),
            "source_kind": source_kind, "role": role, "bindings": bindings or {}}


def _validate_result(result):
    required = {"summary", "measurements", "failures", "decision", "residuals", "next_segment"}
    allowed = required | {"candidate_index", "decisive_examples", "originals", "costs", "counter_refs", "method_refs", "feedback"}
    if not isinstance(result, dict) or not required <= set(result) or set(result) - allowed:
        raise EvidenceError("producer must return summary, measurements, failures, decision, residuals and next_segment")
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        raise EvidenceError("producer summary required")
    if not isinstance(result["measurements"], dict) or not isinstance(result["failures"], dict):
        raise EvidenceError("producer measurements and failure denominators must be explicit objects")
    if set(result["decision"]) != {"choice", "state", "reason", "reconsider_when", "basis_refs"}:
        raise EvidenceError("producer requires the existing explicit scientific decision")
    _texts(result["residuals"])
    _command(result["next_segment"])
    _feedback(result.get("feedback"), result["next_segment"])
    for original in result.get("originals", []):
        if file_identity(original["path"]) != original["identity"]:
            raise EvidenceError("producer original changed")
    for key in ("counter_refs", "method_refs"):
        if not isinstance(result.get(key, []), list):
            raise EvidenceError("producer " + key + " must be a list")
    return _compact(result)


def _publish_note_result(result, work, directory, *, entry, operation_id):
    """Update the existing note memory through its ordinary patch publisher."""
    from .notes import ENTRY, entry_journal, entry_value, patch_progress, read_note
    target = entry_value(entry)
    if target.get("schema") != ENTRY or target["task_id"] != work["task_id"]:
        raise EvidenceError("research work must remain in its selected note task")
    parent_id = work.get("source_record_id")
    if not parent_id:
        raise EvidenceError("note work requires its source record identity")
    parent = read_note(entry_journal(target), parent_id, verify_originals=False)["note"]
    if parent["branch_id"] != work["branch_id"]:
        raise EvidenceError("research work must remain in its selected note branch")
    pins = {name: {"path": str(directory / filename), "identity": file_identity(directory / filename)}
        for name, filename in (("segment_result", "result.json"), ("segment_work", "work.json"),
                               ("segment_input_work", "input-work.json"))}
    originals = {original["name"]: {"path": original["path"], "identity": original["identity"]}
        for original in result.get("originals", [])}
    if set(originals) & set(pins):
        raise EvidenceError("producer original name conflicts with segment output")
    refs = {**originals, **pins}
    counters = list(parent.get("counterevidence", []))
    for counter in result.get("counter_refs", []):
        if isinstance(counter, dict) and set(counter) == {"publication_ref"}:
            if counter["publication_ref"] not in refs:
                raise EvidenceError("counter reference names an absent segment output")
            counter = refs[counter["publication_ref"]]
        if not isinstance(counter, dict) or not {"path", "identity"} <= set(counter):
            raise EvidenceError("note counters require original pins or named segment outputs")
        if counter not in counters:
            counters.append(counter)
    # Replace generated navigation artifacts, preserving actual prior evidence
    # and every counter. Full older work remains in the immutable prior note.
    previous_pins = [parent.get(key) for key in pins]
    evidence = [ref for ref in parent.get("evidence", []) if ref not in previous_pins]
    for ref in refs.values():
        if ref not in evidence:
            evidence.append(ref)
    patch = {"summary": result["summary"], "measurements": result["measurements"],
        "failures": result["failures"], "decision": result["decision"],
        "residuals": result["residuals"], "next_action": result["next_segment"],
        "feedback": result.get("feedback"), "completed_refs": [pins["segment_result"]],
        "evidence": evidence, "counterevidence": counters, **pins}
    # Scientific branch progress can resolve an active question, but cannot
    # revoke an explicit pause/stop represented in the current note controls.
    stopped = {"paused", "stopped", "cancelled", "canceled"}
    if not any(isinstance(parent.get(key), str) and parent[key] in stopped for key in ("state", "status")):
        patch["state"] = result["decision"]["state"]
    if "method_refs" in result:
        patch["method_refs"] = result["method_refs"]
    published = patch_progress(entry, patch, branch_id=work["branch_id"], expected_record=parent_id,
        publication_id=operation_id)
    # current_record_id is the active branch; a producer can publish a background
    # branch too. Check its actual head when determining historical replay.
    current = entry_value(entry)
    head = current.get("branches", {current["branch_id"]: current["record_id"]}).get(work["branch_id"])
    receipt = {"schema": "etalon-segment-publication/1", "status": "published" if head == published["record_id"] else "superseded",
        "entry": str(entry), "operation_id": operation_id, "record_id": published["record_id"],
        "work": pins["segment_work"], "result": pins["segment_result"]}
    receipt_path = directory / "publication-receipt.json"
    if receipt_path.exists():
        prior = _read(receipt_path)
        if {k: v for k, v in prior.items() if k != "status"} != {k: v for k, v in receipt.items() if k != "status"}:
            raise EvidenceError("saved segment publication receipt changed")
    else:
        _save(receipt_path, receipt)
    return receipt


def publish_segment_result(result, work, directory, *, entry, operation_id, parent_entry=None):
    """Publish one saved producer response and its next work object automatically.

    Repeating this exact call reuses the publication request and intent. The
    saved expected entry identity is never recomputed after entry advancement.
    """
    result, work = _validate_result(result), validate_work(work)
    directory, entry = Path(directory).absolute(), Path(entry).absolute()
    directory.mkdir(parents=True, exist_ok=True)
    with _consumer_lock(directory / "publish-segment"):
        compact = {"schema": RESULT, **result}
        _save(directory / "result.json", compact)
        successor = {**work, "residuals": result["residuals"], "next_segment": result["next_segment"],
            "branch_state": result["decision"]["state"], "last_summary": result["summary"],
            "completed_refs": [
                {"path": str(directory / "result.json"), "identity": file_identity(directory / "result.json")}]}
        # Keep the latest actionable feedback, rather than silently applying an
        # older choice to changed conditions. Earlier lessons remain reachable.
        successor.pop("feedback", None)
        if result.get("feedback") is not None:
            successor["feedback"] = result["feedback"]
        work_path = directory / "work.json"
        legacy = work_path.exists() and "previous_work" not in _read(work_path)
        if legacy:
            # Replay pre-feedback publications byte-for-byte; only new segments
            # use bounded lineage. _save still rejects any changed old content.
            successor["completed_refs"] = [*work["completed_refs"], *successor["completed_refs"]]
        else:
            _save(directory / "input-work.json", work)
            successor["previous_work"] = {"path": str(directory / "input-work.json"),
                "identity": file_identity(directory / "input-work.json")}
        _save(directory / "work.json", validate_work(successor))
        if "source_record_id" in work:
            return _publish_note_result(result, work, directory, entry=entry, operation_id=operation_id)
        publication_results = [result_file(directory / "result.json", "segment_result", bindings={"schema": RESULT}),
            result_file(directory / "work.json", "segment_work", bindings={"schema": WORK}), *result.get("originals", [])]
        if not legacy:
            publication_results.append(result_file(directory / "input-work.json", "segment_input_work",
                bindings={"schema": WORK, "work_role": "predecessor"}))
        decision = {**result["decision"], "basis_refs": [*result["decision"]["basis_refs"],
            {"publication_ref": "segment_result"}, {"publication_ref": "segment_work"}]}
        stable = {"entry": str(entry), "operation_id": operation_id, "branch_id": work["branch_id"],
            "results": publication_results, "decision": decision,
            "directory": str(directory / "publication")}
        # Pass named output/existing refs through the established publisher.
        # Empty defaults stay absent to preserve saved request compatibility.
        for key in ("counter_refs", "method_refs"):
            if result.get(key):
                stable[key] = result[key]
        request_path = directory / "publication-request.json"
        if request_path.exists():
            request = _read(request_path)
            if any(request.get(key) != value for key, value in stable.items()):
                raise EvidenceError("segment operation already has different publication content")
            requested_parent = str(Path(parent_entry).absolute()) if parent_entry is not None else None
            if request.get("parent_entry") != requested_parent:
                raise EvidenceError("segment parent changed")
        else:
            target_path = directory / "publication-target.json"
            expected = (_read(target_path)["expected_entry_identity"] if target_path.exists()
                        else file_identity(entry) if entry.exists() else None)
            workspace, checkpoint_ref = load_entry(parent_entry or entry)
            parent = read_content(workspace, checkpoint_ref)
            if parent["task_id"] != work["task_id"]:
                raise EvidenceError("research work must remain in the selected parent task")
            request = {**stable, "expected_entry_identity": expected}
            if parent_entry is not None:
                request["parent_entry"] = str(Path(parent_entry).absolute())
            _save(request_path, request)
        prepared = prepare_result_publication(**request)
        published = resume_publication(prepared["intent_path"])
        receipt = {"schema": "etalon-segment-publication/1", "status": published["status"],
            "entry": str(entry), "operation_id": operation_id, "intent_path": prepared["intent_path"],
            "checkpoint_ref": published.get("checkpoint_ref"),
            "work": {"path": str(directory / "work.json"), "identity": file_identity(directory / "work.json")},
            "result": {"path": str(directory / "result.json"), "identity": file_identity(directory / "result.json")}}
        receipt_path = directory / "publication-receipt.json"
        if receipt_path.exists():
            prior = _read(receipt_path)
            stable_prior = {k: v for k, v in prior.items() if k != "status"}
            stable_now = {k: v for k, v in receipt.items() if k != "status"}
            if stable_prior != stable_now or (prior["status"] != receipt["status"]
                    and (prior["status"], receipt["status"]) != ("published", "superseded")):
                raise EvidenceError("saved segment publication receipt changed")
            # Keep the historical successful receipt; current read may observe
            # a later checkpoint at the entry without reviving this old one.
        else:
            _save(receipt_path, receipt)
        return receipt


def run_segment(work, directory, *, producer, method_id, entry, operation_id, parent_entry=None,
                recover=None):
    """Call a task-owned producer(work, output_directory) once, then publish.

    The producer batches known operations until a new residual requires judgment.
    If the process dies before saving its response, inspect its outputs/handles;
    this adapter will not invoke that producer again on an uncertain outcome.
    An explicit task-owned recover(work, directory) callback may inspect saved
    handles, reuse completed operations and finish the result. It owns native
    recovery; this adapter neither retries scientific work nor infers completion.
    """
    validate_work(work)
    if not isinstance(method_id, str) or not method_id.strip():
        raise EvidenceError("explicit producer method identity required")
    directory = Path(directory).absolute()
    directory.mkdir(parents=True, exist_ok=True)
    identity = {"work": work, "method_id": method_id, "entry": str(Path(entry).absolute()),
        "operation_id": operation_id, "parent_entry": str(Path(parent_entry).absolute()) if parent_entry else None}
    began = time.perf_counter()
    with _consumer_lock(directory / "producer"):
        intent = directory / "producer-intent.json"
        response = directory / "producer-response.json"
        existed = intent.exists()
        if not existed and "source_record_id" in work:
            from .notes import ENTRY, entry_value
            if not Path(entry).exists():
                raise EvidenceError("note segments publish to an existing note entry")
            target = entry_value(entry)
            if (target.get("schema") != ENTRY or target["task_id"] != work["task_id"]
                    or target.get("branches", {target["branch_id"]: target["record_id"]}).get(work["branch_id"])
                    != work["source_record_id"]):
                raise EvidenceError("note entry changed before producer execution")
        _save(intent, identity)
        reused = response.exists()
        if reused:
            saved = _read(response)
            result, producer_wall = saved["result"], saved["producer_wall_seconds"]
        elif existed:
            if recover is None:
                raise EvidenceError("unknown producer outcome; inspect existing outputs/handles, do not automatically repeat")
            started = time.perf_counter()
            result = _validate_result(recover(work, directory))
            producer_wall = None  # The interrupted attempt was not fully timed.
            _save(response, {"result": result, "producer_wall_seconds": producer_wall,
                "recovery_wall_seconds": time.perf_counter() - started,
                "cost_status": "partial; original attempt duration unknown"})
        else:
            # Bind the destination before executing the method. A second writer
            # advancing it during computation must not be silently overwritten.
            _save(directory / "publication-target.json", {
                "expected_entry_identity": file_identity(entry) if Path(entry).exists() else None})
            started = time.perf_counter()
            result = _validate_result(producer(work, directory))
            producer_wall = time.perf_counter() - started
            _save(response, {"result": result, "producer_wall_seconds": producer_wall})
        publication = publish_segment_result(result, work, directory, entry=entry,
            operation_id=operation_id, parent_entry=parent_entry)
    saved_response = _read(response)
    return {**publication, "producer_reused": reused,
        "producer_recovered": "recovery_wall_seconds" in saved_response,
        "producer_wall_seconds": producer_wall,
        "recovery_wall_seconds": saved_response.get("recovery_wall_seconds"),
        "invocation_wall_seconds": time.perf_counter() - began,
        "compact_bytes": file_identity(directory / "result.json")["bytes"] + file_identity(directory / "work.json")["bytes"],
        "host_token_cost": None, "cost_scope": "local adapter/producer elapsed and compact file bytes; no token or scientific gain inference"}


def read_work(entry, *, branch_id=None):
    """Read the selected current work Artifact without opening original arrays.

    This verifies original bytes and checkpoint navigation; its command remains a
    host-selected option. It does not confer execution authority or run anything.
    """
    from .notes import ENTRY, entry_journal, entry_value, read_entry
    metadata = entry_value(entry)
    if metadata.get("schema") == ENTRY:
        current = read_entry(entry, branch_id=branch_id, verify_originals=False)
        note = current["note"]
        pin = note.get("segment_work")
        if pin is None:
            work = work_from_entry(note.get("goal", note["question"]), entry, branch_id=note["branch_id"])
        else:
            resolver = entry_journal(metadata).resolver
            path = resolver.resolve(pin["path"], **pin["identity"]) if resolver is not None else pin["path"]
            if file_identity(path) != pin["identity"] or pin["identity"]["bytes"] > MAX_BYTES:
                raise EvidenceError("current segment work changed or exceeds 64 KiB")
            work = _read(path)
            if (work.get("task_id"), work.get("branch_id")) != (note["task_id"], note["branch_id"]):
                raise EvidenceError("work object belongs to another task or branch")
            work = {**work, "source_entry": str(Path(entry).absolute()), "source_record_id": current["record_id"],
                "last_summary": note["summary"], "residuals": note.get("residuals", work["residuals"]),
                "feedback": note.get("feedback"), "next_action": note.get("next_action"),
                "branch_state": note.get("state", note.get("status", work["branch_state"])),
                "next_segment": note["next_action"] if isinstance(note.get("next_action"), dict)
                    and "argv" in note["next_action"] else None}
            for key in ("conditions", "authorization", "constraints", "controls", "budget", "other_pauses",
                        "state", "status", "pending_handles", "status_guards"):
                if key in note:
                    work[key] = note[key]
            work = _note_choice(validate_work(work))
        return {"work": work, "work_ref": pin or {"record_id": current["record_id"]},
            "record_id": current["record_id"], "entry": str(Path(entry).absolute()),
            "execution_permission": "not_granted"}
    workspace, ref = load_entry(entry)
    checkpoint = read_content(workspace, ref)
    branches = checkpoint["branches"]
    if branch_id is None and len(branches) == 1:
        branch_id = branches[0]["branch_id"]
    branch = next((b for b in branches if b["branch_id"] == branch_id), None)
    if branch is None:
        raise EvidenceError("select an existing branch for continuation")
    candidates = branch["next_read_refs"]
    # Existing publication puts its decision reference last in branch basis.
    # Follow that decision's outputs so cited predecessor work does not become
    # a second, competing current work object.
    if branch["basis_refs"]:
        try:
            decision = read_content(workspace, branch["basis_refs"][-1])
        except ValueError:
            decision = {}
        if (decision.get("schema") == "etalon-research-step/1" and decision.get("operation") == "decision"
                and decision.get("branch_id") == branch_id and decision.get("task_id") == checkpoint["task_id"]):
            candidates = [candidate for candidate in candidates if candidate in decision["output_refs"]]
    matches = []
    for candidate in candidates:
        record = read_evidence(workspace, candidate, view="record", limit=1).get("record", {})
        declared = record.get("bindings", {}).get("schema")
        if record.get("bindings", {}).get("work_role") == "predecessor":
            continue
        if declared is not None and declared != WORK:
            continue
        item = read_evidence(workspace, candidate, view="artifact", limit=32768)
        fragment = item.get("fragment", {})
        if (item.get("verification", {}).get("artifact") != "verified"
                or fragment.get("total_bytes", MAX_BYTES + 1) > MAX_BYTES):
            continue
        parts = [base64.b64decode(fragment["content"])]
        while fragment.get("next_offset") is not None:
            item = read_evidence(workspace, candidate, view="artifact", limit=32768, offset=fragment["next_offset"])
            if item.get("verification", {}).get("artifact") != "verified":
                raise EvidenceError("work artifact changed during pagination")
            fragment = item["fragment"]
            parts.append(base64.b64decode(fragment["content"]))
        try:
            body = json.loads(b"".join(parts).decode("utf-8-sig"))
        except (ValueError, KeyError, UnicodeError):
            continue
        if isinstance(body, dict) and body.get("schema") == WORK:
            if body.get("task_id") != checkpoint["task_id"] or body.get("branch_id") != branch_id:
                raise EvidenceError("work object belongs to another task or branch")
            matches.append((candidate, validate_work(body)))
    if len(matches) != 1:
        raise EvidenceError("current branch must select exactly one work object")
    return {"work_ref": matches[0][0], "work": matches[0][1], "checkpoint_ref": ref,
        "entry": str(Path(entry).absolute()), "execution_permission": "not_granted"}
