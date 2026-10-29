"""Optional bounded native Codex host lifecycle; scientific choices stay in the host.

The task capsule is the independent session's only task-specific prompt. Each
normal host exit leaves a compact checkpoint, then the same persisted session
can resume. External jobs belong to existing ETALON/PRISM handles, not this
adapter. No execution retry, scientific router, job database, or new allocator.

Usage: python examples/research_host_lifecycle.py CAPSULE.json RUN_DIRECTORY
The wall bound admits new host turns; it never kills an in-flight host or job.
For a narrow cwd outside a Git checkout, an existing native profile may set
skip_git_repo_check=true. Absolute capsule paths still locate the task; this
only disables the CLI's Git-directory check and does not change trust/permissions.
An optional context_max_bytes profile field bounds each current-note read
(4096..65536 bytes, default 16384); omitted controls keep explicit read recipes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import time

from etalon.host import _consumer_lock
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.execution_recovery import process_identity
from etalon.runtime.host_wait import inspect_host_process, inspect_wait, validate_wait

CAPSULE = "etalon-host-task-capsule/1"
CHECKPOINT = "etalon-host-checkpoint/1"
STATES = ("continue", "segment_complete", "waiting", "complete", "needs_user")
USAGE_SCOPES = ("thread_cumulative", "turn_delta")
CHECKPOINT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "schema": {"type": "string", "enum": [CHECKPOINT]},
        "task_id": {"type": "string"},
        "state": {"type": "string", "enum": list(STATES)},
        "entry": {"type": "string"},
        "summary": {"type": "string"},
        "basis_refs": {"type": "array", "items": {"type": "string"}},
        "wake_after_seconds": {"type": "integer", "minimum": 0},
    },
    "required": ["schema", "task_id", "state", "entry", "summary", "basis_refs", "wake_after_seconds"],
}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, body):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def context_budget(body):
    value = body.get("context_max_bytes", 16384)
    if type(value) is not int or not 4096 <= value <= 65536:
        raise EvidenceError("context_max_bytes must be an integer in 4096..65536")
    return value


def validate_capsule(body):
    if not isinstance(body, dict) or body.get("schema") != CAPSULE:
        raise EvidenceError("host task capsule schema required")
    for name in ("task_id", "entry", "question", "scope", "authority", "completion_condition", "host_instructions"):
        if not isinstance(body.get(name), str) or not body[name].strip():
            raise EvidenceError("host task capsule requires " + name)
    for name in ("entry",):
        if not Path(body[name]).is_absolute():
            raise EvidenceError(name + " must be absolute")
    if not isinstance(body.get("budget"), dict) or not body["budget"]:
        raise EvidenceError("explicit host/scientific budget required")
    context_budget(body)
    native = body.get("native", {})
    if not isinstance(native, dict):
        raise EvidenceError("explicit native runtime profile required")
    for name in ("codex_executable", "cwd", "model", "reasoning_effort"):
        if not isinstance(native.get(name), str) or not native[name].strip():
            raise EvidenceError("explicit native " + name + " required")
    for name in ("codex_executable", "cwd"):
        if not Path(native[name]).is_absolute():
            raise EvidenceError("native " + name + " must be absolute")
    if "skip_git_repo_check" in native and type(native["skip_git_repo_check"]) is not bool:
        raise EvidenceError("native skip_git_repo_check must be an explicit boolean")
    if "usage_scope" in native and native["usage_scope"] not in USAGE_SCOPES:
        raise EvidenceError("native usage_scope must be thread_cumulative or turn_delta")
    bounds = body.get("bounds", {})
    for name in ("max_host_turns", "max_wall_seconds"):
        if type(bounds.get(name)) is not int or bounds[name] <= 0:
            raise EvidenceError("positive integer " + name + " required")
    if len(json.dumps(body).encode("utf-8")) > 65536:
        raise EvidenceError("task capsule exceeds compact 64 KiB bound")
    if body.get("session_mode", "resume") not in {"resume", "fresh"}:
        raise EvidenceError("host session mode must be resume or fresh")
    if "continuation" in body:
        continuation = body["continuation"]
        if (not isinstance(continuation, dict) or set(continuation) != {
                "objective", "completion_condition", "authorized_actions", "resume_after_host_exit"}
                or any(not isinstance(continuation.get(key), str) or not continuation[key].strip()
                       for key in ("objective", "completion_condition"))
                or not isinstance(continuation.get("authorized_actions"), list)
                or not continuation["authorized_actions"]
                or any(not isinstance(action, str) or not action.strip() for action in continuation["authorized_actions"])
                or type(continuation.get("resume_after_host_exit")) is not bool):
            raise EvidenceError("continuation requires objective, completion_condition, explicit authorized_actions and boolean resume_after_host_exit")
    return body


def capsule_from_goal(goal, entry, profile, *, branch_id=None, work=None):
    """Reuse a runtime/authority profile and an existing entry, without a manual capsule.

    The profile supplies genuine authority, resources and native configuration;
    natural language never manufactures those fields. A published work object is
    enough for fresh hosts; original evidence remains available through its refs.
    """
    from etalon.research.notes import ENTRY, entry_value, read_current
    entry = str(Path(entry).absolute())
    max_context = context_budget(profile)
    if not isinstance(goal, str) or not goal.strip():
        raise EvidenceError("natural research goal required")
    value = entry_value(entry)
    if value.get("schema") == ENTRY:
        if work is not None:
            raise EvidenceError("note entry uses its current reader, not a legacy work override")
        current = read_current(entry, branch_id=branch_id, max_bytes=max_context)
        initial = {"initial_context": current, "branch_id": current["branch_id"],
                   "read_recipe": value.get("read_recipe"),
                   "branch_catalog": {"entry": entry,
                       "sdk": "etalon.research.resource_catalog.build_resource_catalog",
                       "next_read": {"limit": 20, "max_bytes": 16384}}}
        task_id = current["task_id"]
    else:
        from etalon.research.research_segment import read_work, work_from_entry, validate_work
        if work is None:
            try:
                work = read_work(entry, branch_id=branch_id)["work"]
            except EvidenceError as exc:
                if "exactly one work object" not in str(exc):
                    raise
                work = work_from_entry(goal, entry, branch_id=branch_id)
        validate_work(work)
        task_id, initial = work["task_id"], {"initial_work": work}
    capsule = {"schema": CAPSULE, "task_id": task_id, "entry": entry,
        "question": goal, "scope": profile.get("scope", goal),
        "authority": profile.get("authority"), "budget": profile.get("budget"),
        "completion_condition": profile.get("completion_condition", goal),
        "host_instructions": profile.get("host_instructions", "Use the current work object; batch known operations until new evidence requires judgment."),
        "native": profile.get("native"), "bounds": profile.get("bounds"),
        "session_mode": profile.get("session_mode", "resume"), **initial}
    if "context_max_bytes" in profile:
        capsule["context_max_bytes"] = max_context
    if "continuation" in profile:
        capsule["continuation"] = profile["continuation"]
    return validate_capsule(capsule)


def validate_checkpoint(body, capsule):
    if (not isinstance(body, dict) or set(body) != set(CHECKPOINT_SCHEMA["required"])
            or body.get("schema") != CHECKPOINT or body.get("task_id") != capsule["task_id"]
            or body.get("entry") != capsule["entry"] or body.get("state") not in STATES
            or not isinstance(body.get("summary"), str) or not body["summary"].strip()
            or not isinstance(body.get("basis_refs"), list)
            or any(not isinstance(ref, str) or not ref for ref in body["basis_refs"])
            or type(body.get("wake_after_seconds")) is not int or body["wake_after_seconds"] < 0):
        raise EvidenceError("invalid host checkpoint; normal exit is not scientific completion")
    return body


def native_argv(capsule, folder, session_id):
    native = capsule["native"]
    argv = [native["codex_executable"], "exec"]
    argv += ["resume", session_id] if session_id else ["-C", native["cwd"]]
    if native.get("skip_git_repo_check") is True:
        argv.append("--skip-git-repo-check")
    # Preserve the invoking host's configured trust unless the existing profile
    # explicitly declares these options. A goal never widens runtime authority.
    for key in ("approval_policy", "sandbox_mode"):
        if key in native:
            argv += ["-c", key + "=" + json.dumps(native[key])]
    return argv + ["-m", native["model"], "-c", "model_reasoning_effort=" + json.dumps(native["reasoning_effort"]),
        "--json", "--output-schema", str(folder.parent / "checkpoint-schema.json"),
        "-o", str(folder / "checkpoint.json"), "-"]


def prompt_for(capsule, folder, session_id):
    boundary = """You are the scientific host of an optional continuing research session.
The capsule below is the task authority. Make scientific choices yourself inside that scope.
Read the current scientific entry and the relevant originals needed for the next decision.
Reuse completed calculations, prior proposals and saved publication intents; inspect existing
native handles before considering any execution. External compute survives your host exit.
A finished subgoal is a checkpoint, not completion of the lasting scientific objective.
At a useful bounded checkpoint, persist scientific progress using existing entry/publication
interfaces, then exit normally with the required JSON host checkpoint. State complete only
when the active scientific completion condition is actually met; continue for unfinished authorized
work, waiting for pending work, or needs_user for a concrete dependency on new authority.
This JSON controls only host wakeup, never scientific acceptance. Keep tool output compact.
Use basis_refs for concrete entry/artifact paths or evidence identifiers. Do not edit the
capsule or adapter receipts. No full parent conversation has been injected into this session.
"""
    if capsule.get("continuation"):
        boundary += """The continuation.objective is the lasting scientific question. The top-level
completion_condition describes this local segment; satisfying it uses segment_complete
and immediately continues authorized research. Use complete only when the separate
continuation.completion_condition is met. A known method or a finished engineering
acceptance check alone does not answer that question. Choose the next useful scientific
action yourself; no mandatory probe, algorithm, phase order or improvement is imposed.
continuation.authorized_actions records existing scoped permission. Carry out those
actions within the authority, budget and current pause constraints without asking for
the same permission again. Use needs_user only for a specific missing input or action
outside existing authority; describe that dependency and cite its actual constraint.
Host turn/wall bounds suspend the adapter; they do not make the objective complete.
"""
    if session_id:
        boundary += "Your previous native host process exited. Resume this SAME saved session and reread the current entry; do not repeat completed work.\n"
    context = dict(capsule)
    if "initial_context" in context:
        from etalon.research.notes import entry_value, read_current
        value = entry_value(capsule["entry"])
        context["current_context"] = read_current(capsule["entry"], branch_id=capsule["branch_id"],
                                                max_bytes=context_budget(capsule))
        if context["current_context"]["task_id"] != capsule["task_id"]:
            raise EvidenceError("current note belongs to a different task")
        context["read_recipe"] = value.get("read_recipe")
        del context["initial_context"]
        boundary = boundary.replace(
            "Read the current scientific entry and the relevant originals needed for the next decision.\n",
            "Use current_context below, its declared scope, definitions, completed results and pending handles.\n")
        boundary += ("Expand needed omitted fields through the supplied pinned details/followup reading recipe.\n"
                     "Omitted authorization, pauses and constraints remain unread; use followup.controls before assuming their scope.\n"
                     "Use a known next_action directly within existing authority; keep understood numerical steps in its ordinary code batch.\n"
                     "Use compact feedback to decide whether to reuse, revise a method or choose a different computation; expand only relevant omitted fields.\n"
                     "A cheaper method may replace work within its declared decision/fidelity scope, with an explicit fallback outside that scope.\n"
                     "Unexecuted measurements stay unknown. A retained method or a negative candidate does not by itself end the lasting objective.\n")
    if "initial_work" in context:
        from etalon.research.research_segment import read_work
        try:
            context["current_work"] = read_work(capsule["entry"], branch_id=context["initial_work"]["branch_id"])["work"]
        except EvidenceError as exc:
            if "exactly one work object" not in str(exc):
                raise
            context["current_work"] = context["initial_work"]
        del context["initial_work"]
        boundary = boundary.replace(
            "Read the current scientific entry and the relevant originals needed for the next decision.\n",
            "Use the verified current_work below as the current scientific context.\n")
        boundary += """The compact current_work preserves the unresolved goal and an optional executable next
segment. For an unchanged known method, use that command and its pinned inputs directly;
do not reconstruct it by reading producer/adapter source, publication intents/workspaces,
or the completed source history. Expand a specific original when an actual scientific
contradiction, changed dependency or execution error needs it. A successful segment
receipt already identifies its publication and outputs; inspect the compact result and
residual rather than separately reprinting every receipt, work and source array.
Known mechanical followups belong inside the ordinary producer's batch, including
conditional steps whose interpretation is already settled. Return to host judgment when
the residual changes what is worth doing, not after every known operation. This does not
override applicable workspace instructions or prevent choosing a different scientific
action when the compact evidence is insufficient.
"""
    previous = folder.parent / "receipt.json"
    if previous.exists():
        prior = read(previous)
        if prior.get("task_id") != capsule["task_id"]:
            raise EvidenceError("previous lifecycle receipt belongs to a different task")
        context["previous_checkpoint"] = prior.get("last_checkpoint")
        if prior.get("wait_observation"):
            context["wait_observation"] = prior["wait_observation"]
        if prior.get("host_recovery"):
            context["host_recovery"] = prior["host_recovery"]
            boundary += """The prior host has provably exited with an interrupted or invalid final turn.
Its scientific outcome is unresolved. Reconcile the current entry, durable job handles,
receipts and actual outputs before selecting any further execution. Do not resubmit a
prior command or infer scientific failure from host exit. Reuse finished results, inspect
still-running external jobs and recover publication through existing task-local APIs.
The adapter has resumed only host judgment; it has not retried any scientific action.
"""
    boundary += """For waiting on an existing job, optionally write wait.json in this turn directory:
{"schema":"etalon-host-wait/1","poll_seconds":5,"checks":[...]}. Use checks with
kind native_attempt, absolute path, exact request_id and attempt_id; or kind file
with an absolute completion-marker path; or kind json_status with an absolute
path, top-level field and disjoint running/ready/failed string lists. Missing
files mean still running unless missing_is_running=false. Checks are read-only;
ready/failed wakes you to interpret the result, never declares scientific success.
Use only task-owned completion signals. A failed check stops visibly, without
repeated LLM polling or execution retry. Legacy checkpoints without wait.json
retain their explicit wake_after_seconds timer. Do not poll with repeated LLM turns.
If existing child usage receipts expose exact session/usage or request IDs, you
may link them in usage-sources.json as a list of {session_id, usage_id, receipt,
identity}, where receipt is absolute and identity is its {sha256, bytes}. The
receipt must contain those IDs and measured usage. Do not invent IDs or costs.
"""
    return boundary + "Host turn artifacts: " + str(folder) + "\nTask capsule:\n" + json.dumps(context, ensure_ascii=False, indent=2)


def host_turn(capsule, folder, session_id):
    """One real process; stream usage once and retain raw events without replaying them."""
    folder.mkdir()
    argv = native_argv(capsule, folder, session_id)
    prompt = prompt_for(capsule, folder, session_id)
    (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
    started, clock = stamp(), time.perf_counter()
    intent = {"schema": "etalon-host-turn-intent/1", "argv": argv, "started_at": started,
              "resumed_session_id": session_id, "capsule_identity": file_identity(folder.parent / "capsule.json")}
    save(folder / "intent.json", intent)
    usages, event_counts = [], {}
    observed_session = None
    with (folder / "events.jsonl").open("x", encoding="utf-8") as events, (folder / "stderr.txt").open("x", encoding="utf-8") as errors:
        process = subprocess.Popen(argv, cwd=capsule["native"]["cwd"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        identity = process_identity(process.pid)
        save(folder / "process.json", {"pid": process.pid, "identity": identity, "started_at": started})
        process.stdin.write(prompt)
        process.stdin.close()
        for line in process.stdout:
            events.write(line)
            events.flush()
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type", "unknown")
            event_counts[kind] = event_counts.get(kind, 0) + 1
            if kind == "thread.started":
                observed_session = event["thread_id"]
                save(folder / "session.json", {"session_id": observed_session})
            if kind == "turn.completed":
                usage = event.get("usage")
                if isinstance(usage, dict):
                    usage = dict(usage)
                    native_id = event.get("usage_id") or event.get("request_id") or event.get("turn_id")
                    if isinstance(native_id, str) and native_id:
                        usage["usage_id"] = native_id
                usages.append(usage)
        returncode = process.wait()
    checkpoint, checkpoint_error = None, None
    try:
        checkpoint = validate_checkpoint(read(folder / "checkpoint.json"), capsule)
    except (OSError, ValueError, EvidenceError) as exc:
        checkpoint_error = str(exc)
    receipt = {"schema": "etalon-host-turn-receipt/1", "started_at": started, "finished_at": stamp(),
        "wall_seconds": time.perf_counter() - clock, "process": identity, "pid": process.pid,
        "prompt_bytes": len(prompt.encode("utf-8")),
        "session_id": observed_session, "resumed_session_id": session_id,
        "session_continuity_verified": bool(observed_session) and (not session_id or observed_session == session_id),
        "exit_code": returncode, "usage": usages, "event_counts": event_counts,
        "usage_scope": capsule["native"].get("usage_scope", "thread_cumulative"),
        "checkpoint": checkpoint, "checkpoint_error": checkpoint_error,
        "checkpoint_identity": file_identity(folder / "checkpoint.json") if (folder / "checkpoint.json").exists() else None,
        "artifacts": {name: str(folder / name) for name in ("events.jsonl", "stderr.txt", "prompt.txt", "checkpoint.json")},
        "scientific_completion_inferred_from_exit": False}
    save(folder / "receipt.json", receipt)
    return receipt


def reconcile_exited_host(capsule, folder, session_id, observation, observed_record, *, record_receipt=True):
    """Account for an interrupted host turn; do not infer its scientific outcome.

    Only an explicitly authorized continuation may resume host judgment. The
    immutable launch intent and native session event must identify the same turn.
    An absent checkpoint remains absent. Original runtime and final usage remain
    unknown instead of being replaced by the elapsed time until rediscovery.
    """
    if (observation.get("state") != "exited"
            or not capsule.get("continuation", {}).get("resume_after_host_exit", False)):
        return None
    intent = read(folder / "intent.json")
    process = read(folder / "process.json")
    if not isinstance(intent, dict) or not isinstance(process, dict):
        raise EvidenceError("interrupted host requires object launch/process records")
    if process != observed_record:
        raise EvidenceError("host process record changed after the exit observation")
    selected_session = session_id if capsule.get("session_mode", "resume") == "resume" else None
    if (intent.get("schema") != "etalon-host-turn-intent/1"
            or intent.get("capsule_identity") != file_identity(folder.parent / "capsule.json")
            or intent.get("resumed_session_id") != selected_session
            or intent.get("argv") != native_argv(capsule, folder, selected_session)
            or not isinstance(intent.get("started_at"), str)
            or process.get("started_at") != intent["started_at"]):
        raise EvidenceError("interrupted host launch differs from the saved capsule/session")
    session_record = read(folder / "session.json")
    session = session_record.get("session_id") if isinstance(session_record, dict) else None
    if not isinstance(session, str) or not session or (selected_session and session != selected_session):
        raise EvidenceError("interrupted native session is absent or differs")
    usages, event_counts, sessions = [], {}, set()
    with (folder / "events.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue  # An interrupted final JSON line does not prove completion.
            if not isinstance(event, dict):
                continue
            kind = event.get("type", "unknown")
            if not isinstance(kind, str):
                continue
            event_counts[kind] = event_counts.get(kind, 0) + 1
            if kind == "thread.started":
                if not isinstance(event.get("thread_id"), str):
                    raise EvidenceError("interrupted native session event is invalid")
                sessions.add(event["thread_id"])
            if kind == "turn.completed":
                usage = event.get("usage")
                if isinstance(usage, dict):
                    usage = dict(usage)
                    native_id = event.get("usage_id") or event.get("request_id") or event.get("turn_id")
                    if isinstance(native_id, str) and native_id:
                        usage["usage_id"] = native_id
                usages.append(usage)
    if sessions != {session}:
        raise EvidenceError("interrupted host lacks a consistent native session event")
    if not Path(capsule["entry"]).is_file():
        raise EvidenceError("interrupted host requires its existing scientific entry")
    checkpoint, checkpoint_error = None, None
    try:
        checkpoint = validate_checkpoint(read(folder / "checkpoint.json"), capsule)
    except (OSError, ValueError, EvidenceError) as exc:
        checkpoint_error = str(exc)
    receipt = {"schema": "etalon-host-turn-receipt/1", "started_at": intent["started_at"],
        "finished_at": None, "reconciled_at": stamp(), "wall_seconds": None,
        "process": process["identity"], "pid": process["pid"],
        "prompt_bytes": (folder / "prompt.txt").stat().st_size,
        "session_id": session, "resumed_session_id": selected_session,
        "session_continuity_verified": True, "exit_code": None,
        "usage": usages, "usage_incomplete": True, "event_counts": event_counts,
        "usage_scope": capsule["native"].get("usage_scope", "thread_cumulative"),
        "checkpoint": checkpoint, "checkpoint_error": checkpoint_error,
        "checkpoint_identity": file_identity(folder / "checkpoint.json") if (folder / "checkpoint.json").exists() else None,
        "reconciliation_required": True,
        "host_recovery": {"process_observation": observation, "scientific_outcome": "unresolved",
            "action": "resume_host_to_reconcile_existing_handles", "scientific_retry": False,
            "intent_identity": file_identity(folder / "intent.json"),
            "process_record_identity": file_identity(folder / "process.json"),
            "session_record_identity": file_identity(folder / "session.json")},
        "artifacts": {name: str(folder / name) for name in ("events.jsonl", "stderr.txt", "prompt.txt", "checkpoint.json")},
        "scientific_completion_inferred_from_exit": False}
    if record_receipt:
        save(folder / "receipt.json", receipt)
    return receipt


def continue_recorded_interruption(capsule, folder, session_id, receipt):
    """Keep a failed receipt intact while resuming explicitly authorized judgment."""
    if (receipt.get("reconciliation_required")
            or (receipt.get("exit_code") == 0 and receipt.get("checkpoint") is not None)
            or not capsule.get("continuation", {}).get("resume_after_host_exit", False)
            or not receipt.get("session_id") or receipt.get("session_continuity_verified") is not True):
        return receipt
    try:
        record = read(folder / "process.json")
        observation = inspect_host_process(record)
        recovered = reconcile_exited_host(capsule, folder, session_id, observation, record, record_receipt=False)
        if recovered is None or recovered["session_id"] != receipt["session_id"]:
            return receipt
        if (receipt.get("schema") != "etalon-host-turn-receipt/1"
                or receipt.get("pid") != recovered["pid"] or receipt.get("process") != recovered["process"]
                or receipt.get("resumed_session_id") != recovered["resumed_session_id"]):
            raise EvidenceError("failed receipt differs from its recorded host process/session")
        source_identity = file_identity(folder / "receipt.json")
        recovery_path = folder / "recovery.json"
        if recovery_path.exists():
            recovery = read(recovery_path)
            if (not isinstance(recovery, dict) or recovery.get("schema") != "etalon-host-recovery/1"
                    or recovery.get("receipt_identity") != source_identity
                    or recovery.get("session_id") != receipt["session_id"]
                    or not isinstance(recovery.get("host_recovery"), dict)):
                raise EvidenceError("host recovery differs from the original failure receipt")
            for key in ("intent_identity", "process_record_identity", "session_record_identity"):
                if recovery["host_recovery"][key] != recovered["host_recovery"][key]:
                    raise EvidenceError("host recovery evidence changed")
        else:
            recovery = {"schema": "etalon-host-recovery/1", "recorded_at": stamp(),
                "receipt_identity": source_identity, "session_id": receipt["session_id"],
                "host_recovery": {**recovered["host_recovery"],
                    "original_failed_receipt": str(folder / "receipt.json"),
                    "original_failed_receipt_identity": source_identity}}
            save(recovery_path, recovery)
        # Only the in-memory view is augmented. Preserve the original duration,
        # failure, checkpoint and usage; unobserved interrupted usage is unknown.
        return {**receipt, "host_recovery": recovery["host_recovery"],
                "reconciliation_required": True, "usage_incomplete": True}
    except (OSError, ValueError, KeyError, EvidenceError) as exc:
        return {**receipt, "recovery_error": str(exc)[:500]}


def usage_summary(receipts, directory, *, native_usage_scope=None):
    """Sum measured native usage and explicitly linked child receipts only.

    usage-sources.json is an optional list of {session_id, usage_id, receipt,
    identity}. The existing receipt must carry those exact IDs and a usage dict.
    Native Codex counters are explicitly thread_cumulative in new receipts. An
    explicit profile fallback can interpret older native receipts. Untagged
    callers retain turn_delta semantics; no counter-value heuristic is used.
    No directory/time inference, provider-price estimate or second cost ledger.
    """
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    totals = {key: 0 for key in keys}
    unknown, measured, costs, records, linked, issues = 0, 0, [], {}, 0, []
    missing_thread_usage = []
    thread_origins, response_ids, response_records = set(), set(), 0

    def add(value, identity, *, source, scope="turn_delta", session_id=None,
            resumed_session_id=None, position=0, complete=True):
        record = {"value": value, "scope": scope, "session_id": session_id,
            "resumed_session_id": resumed_session_id, "position": position,
            "complete": complete, "source": source}
        if identity in records:
            previous = records[identity]
            if previous is None or any(previous[k] != record[k] for k in ("value", "scope", "session_id")):
                issues.append({"source": source, "error": "same exact usage ID has conflicting values"})
                records[identity] = None
            return
        records[identity] = record

    for number, item in enumerate(receipts, 1):
        scope = item.get("usage_scope", native_usage_scope or "turn_delta")
        session = item.get("session_id")
        if scope == "thread_cumulative" and session and item.get("resumed_session_id") is None:
            thread_origins.add(session)
        if not item["usage"] or item.get("usage_incomplete"):
            if scope == "thread_cumulative" and isinstance(session, str) and session:
                missing_thread_usage.append((session, number))
            else:
                unknown += 1
        for index, turn in enumerate(item["usage"]):
            uid = turn.get("usage_id") or turn.get("request_id") if isinstance(turn, dict) else None
            add(turn, (session, uid) if uid else (str(directory), number, index), source="host",
                scope=scope, session_id=session, resumed_session_id=item.get("resumed_session_id"),
                position=number, complete=not item.get("usage_incomplete", False))
    for number in range(1, len(receipts) + 1):
        declaration = directory / ("turn-%03d" % number) / "usage-sources.json"
        if not declaration.exists():
            continue
        try:
            if declaration.stat().st_size > 65536:
                raise EvidenceError("usage sources exceed compact bound")
            sources = read(declaration)
            if not isinstance(sources, list) or len(sources) > 100:
                raise EvidenceError("usage sources must be at most 100 explicit receipt links")
            for source in sources:
                try:
                    if (not isinstance(source, dict) or any(not isinstance(source.get(k), str) or not source[k]
                            for k in ("session_id", "usage_id", "receipt")) or not Path(source["receipt"]).is_absolute()):
                        raise EvidenceError("exact session/usage IDs and absolute receipt required")
                    if Path(source["receipt"]).stat().st_size > 1024 * 1024:
                        raise EvidenceError("linked usage receipt exceeds 1 MiB")
                    if file_identity(source["receipt"]) != source.get("identity"):
                        raise EvidenceError("linked usage receipt identity changed or absent")
                    linked_receipt = read(source["receipt"])
                    if not isinstance(linked_receipt, dict) or linked_receipt.get("session_id") != source["session_id"]:
                        raise EvidenceError("linked usage receipt IDs differ")
                    child_usage = linked_receipt.get("usage")
                    if isinstance(child_usage, list):
                        matching = [u for u in child_usage if isinstance(u, dict)
                            and (u.get("usage_id") or u.get("request_id")) == source["usage_id"]]
                        if len(matching) != 1:
                            raise EvidenceError("linked native turn must expose exactly one matching usage ID")
                        child_usage = matching[0]
                    elif (linked_receipt.get("usage_id") or linked_receipt.get("request_id")) != source["usage_id"]:
                        raise EvidenceError("linked usage receipt IDs differ")
                    if isinstance(child_usage, dict) and linked_receipt.get("response_id") == source["usage_id"]:
                        child_usage = {**child_usage, "response_id": linked_receipt["response_id"]}
                    add(child_usage, (source["session_id"], source["usage_id"]), source=source["receipt"],
                        scope=linked_receipt.get("usage_scope", "turn_delta"), session_id=source["session_id"],
                        resumed_session_id=linked_receipt.get("resumed_session_id"),
                        position=number, complete=not linked_receipt.get("usage_incomplete", False))
                    linked += 1
                except (OSError, ValueError, KeyError, EvidenceError) as exc:
                    unknown += 1
                    issues.append({"source": str(source)[:300], "error": str(exc)[:300]})
        except (OSError, ValueError, EvidenceError) as exc:
            unknown += 1
            issues.append({"source": str(declaration), "error": str(exc)[:300]})
    thread_counters, invalid_threads = {}, set()
    observed_scopes = set()
    session_scopes = {}
    for record in records.values():
        if record is not None and isinstance(record["session_id"], str) and record["session_id"]:
            session_scopes.setdefault(record["session_id"], set()).add(record["scope"])
    ambiguous_sessions = {session for session, scopes in session_scopes.items() if len(scopes) > 1}
    for session in sorted(ambiguous_sessions):
        issues.append({"source": "usage_summary", "session_id": session,
                       "error": "mixed cumulative and delta scopes for the same session; overlap is unknown"})
    for record in records.values():
        value = record["value"] if record is not None else None
        if (not isinstance(value, dict) or any(type(value.get(k)) is not int or value[k] < 0 for k in keys)
                or value["cached_input_tokens"] > value["input_tokens"]):
            unknown += 1
            continue
        scope, session = record["scope"], record["session_id"]
        observed_scopes.add(scope)
        if session in ambiguous_sessions:
            unknown += 1
            continue
        if scope not in USAGE_SCOPES:
            unknown += 1
            issues.append({"source": record["source"], "error": "unknown usage scope"})
            continue
        charged = value
        if scope == "thread_cumulative":
            if not isinstance(session, str) or not session or session in invalid_threads:
                unknown += 1
                continue
            previous = thread_counters.get(session)
            if previous is None and record["resumed_session_id"] is not None and session not in thread_origins:
                unknown += 1
                invalid_threads.add(session)
                issues.append({"source": record["source"], "error": "resumed cumulative usage has no baseline in this run"})
                continue
            if previous is None:
                previous = {key: 0 for key in keys}
            charged = {key: value[key] - previous[key] for key in keys}
            if any(charged[key] < 0 for key in keys) or charged["cached_input_tokens"] > charged["input_tokens"]:
                unknown += 1
                invalid_threads.add(session)
                issues.append({"source": record["source"], "error": "cumulative usage counter regressed or changed scope", "session_id": session})
                continue
            thread_counters[session] = value
            cost, previous_cost = value.get("cost_usd"), previous.get("cost_usd", 0 if not any(previous[key] for key in keys) else None)
            if (type(cost) in (int, float) and math.isfinite(cost) and type(previous_cost) in (int, float)
                    and math.isfinite(previous_cost) and cost >= previous_cost >= 0):
                charged["cost_usd"] = cost - previous_cost
        measured += 1
        if scope == "turn_delta" and isinstance(value.get("response_id"), str) and value["response_id"]:
            response_ids.add((session, value["response_id"]))
            response_records += 1
        for key in keys:
            totals[key] += charged[key]
        cost = charged.get("cost_usd")
        if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
            costs.append(cost)
    # Real native recovery can omit the interrupted tool-call response from its
    # resumed CLI counter, even though that response has a durable usage record.
    # Later monotone snapshots therefore cannot clear an interrupted turn's
    # unknown usage. Exact response-ID reconciliation belongs to explicit evidence.
    unknown += len(missing_thread_usage)
    usage = {key: totals[key] if measured else None for key in keys}
    usage["uncached_input_tokens"] = totals["input_tokens"] - totals["cached_input_tokens"] if measured else None
    usage.update(unknown_usage_records_or_turns=unknown, measured_usage_records=measured,
        coverage="partial" if unknown else "declared_records_complete",
        cost_usd=sum(costs) if costs and len(costs) == measured and not unknown else None,
        known_cost_usd=sum(costs) if costs else None,
        unknown_cost_records=measured - len(costs) + unknown,
        explicit_child_receipt_links=linked, issues=issues,
        accounting="explicit turn deltas plus monotone per-session cumulative deltas",
        observed_usage_scopes=sorted(observed_scopes),
        model_response_count=len(response_ids) if measured and response_records == measured and not unknown else None,
        known_model_response_count=len(response_ids) if response_ids else None,
        scope="host plus exact-ID declared child receipts; undeclared child/provider usage remains unknown")
    return usage


def summarize(capsule, directory, receipts, stop_reason, active=None):
    usage = usage_summary(receipts, directory, native_usage_scope=capsule["native"].get("usage_scope"))
    checkpoint = receipts[-1]["checkpoint"] if receipts else None
    wait_path = directory / ("turn-%03d" % len(receipts)) / "wait-state.json"
    wait = read(wait_path) if wait_path.exists() else None
    wait_states = [read(path) for number in range(1, len(receipts) + 1)
        if (path := directory / ("turn-%03d" % number) / "wait-state.json").exists()]
    began = datetime.fromisoformat(read(directory / "started.json")["started_at"])
    known_host_wall = sum(item["wall_seconds"] for item in receipts if item.get("wall_seconds") is not None)
    unknown_host_wall = sum(item.get("wall_seconds") is None for item in receipts)
    result = {"schema": "etalon-host-lifecycle-receipt/1", "task_id": capsule["task_id"],
        "entry": capsule["entry"], "capsule_identity": file_identity(directory / "capsule.json"),
        "session_id": receipts[-1]["session_id"] if receipts else None,
        "host_turns": len(receipts), "host_wall_seconds": None if unknown_host_wall else known_host_wall,
        "known_host_wall_seconds": known_host_wall, "unknown_host_wall_turns": unknown_host_wall,
        "prompt_bytes": sum(item.get("prompt_bytes", 0) for item in receipts),
        "session_mode": capsule.get("session_mode", "resume"),
        "lifecycle_elapsed_seconds": (datetime.now(timezone.utc) - began).total_seconds(),
        "elapsed_basis": "includes host processes, controller work, waits and any interval before controller reentry",
        "usage": usage, "stop_reason": stop_reason, "last_checkpoint": checkpoint,
        "host_recovery": receipts[-1].get("host_recovery") if receipts else None,
        "host_recovery_error": receipts[-1].get("recovery_error") if receipts else None,
        "completion_scope": "continuation_objective" if capsule.get("continuation") else "capsule_condition",
        "objective_complete": bool(checkpoint and checkpoint["state"] == "complete" and stop_reason == "host_complete"),
        "wait_observation": wait,
        "wait_checks": sum(item.get("check_count", 0) for item in wait_states),
        "wait_check_wall_seconds": sum(item.get("read_wall_seconds", 0) for item in wait_states),
        "wait_check_errors": sum(item.get("check_error_count", 0) for item in wait_states),
        "active_process": active, "updated_at": stamp(),
        "turn_receipts": [str(directory / ("turn-%03d" % n) / "receipt.json") for n in range(1, len(receipts) + 1)],
        "external_compute_terminated": False, "wall_bound": "new_turn_admission_only; never kills in-flight work"}
    save(directory / "receipt.json", result)
    return result


def await_completion(capsule, directory, receipts, began):
    """Observe a declared existing task until it changes or this run is bounded."""
    folder = directory / ("turn-%03d" % len(receipts))
    declaration = folder / "wait.json"
    if not declaration.exists():
        return None  # Legacy explicit timer remains compatible.
    state_path = folder / "wait-state.json"
    previous = read(state_path) if state_path.exists() else {}
    count, read_wall = previous.get("check_count", 0), previous.get("read_wall_seconds", 0)
    errors, last_error = previous.get("check_error_count", 0), previous.get("last_check_error")
    try:
        if declaration.stat().st_size > 65536:
            raise EvidenceError("wait declaration exceeds compact bound")
        declared = read(declaration)
        shared = declared.get("schema") == "etalon-wait-handle/1"
        if shared:
            from etalon.runtime.host_wait import read_wait
            spec = validate_wait(read(Path(declared["directory"]) / "declaration.json"))
        else:
            spec = validate_wait(declared)
        identity = file_identity(declaration)
        if previous.get("declaration_identity") is not None and previous["declaration_identity"] != identity:
            raise EvidenceError("waiting declaration changed; do not silently alter a pending handle")
        for index in range(spec.get("max_checks", 1200)):
            if file_identity(declaration) != identity:
                raise EvidenceError("waiting declaration changed during observation")
            started_read = time.perf_counter()
            observed = read_wait(declared) if shared else inspect_wait(spec)
            elapsed_read = time.perf_counter() - started_read
            count += 1
            if shared:
                observed["source_check_count"] = observed.pop("check_count", 0)
                observed.pop("read_wall_seconds", None)
                read_wall += elapsed_read
            else:
                read_wall += observed.pop("read_wall_seconds")
            if observed["state"] == "check_error":
                errors += 1
                last_error = {"checked_at": stamp(), "checks": observed["checks"]}
            observed.update(checked_at=stamp(), check_count=count, read_wall_seconds=read_wall,
                            declaration_identity=identity, check_error_count=errors, last_check_error=last_error)
            save(state_path, observed)
            if observed["state"] in {"ready", "failed"}:
                summarize(capsule, directory, receipts, "wait_" + observed["state"])
                return None
            if observed["state"] in {"check_error", "unknown"}:
                return "wait_" + observed["state"]
            if observed["state"] == "wait_check_bound":
                return observed["state"]
            if index + 1 == spec.get("max_checks", 1200):
                return "wait_check_bound"
            next_check = time.monotonic() + spec.get("poll_seconds", 5)
            while next_check > time.monotonic():
                remaining = capsule["bounds"]["max_wall_seconds"] - (datetime.now(timezone.utc) - began).total_seconds()
                if remaining <= 0:
                    return "wall_admission_bound"
                time.sleep(min(next_check - time.monotonic(), remaining, 30))
        return "wait_check_bound"
    except (OSError, ValueError, EvidenceError) as exc:
        save(state_path, {"state": "check_error", "error": str(exc)[:500], "checked_at": stamp(),
                          "check_count": count, "read_wall_seconds": read_wall,
                          "check_error_count": errors + 1, "last_check_error": str(exc)[:500],
                          "declaration_identity": previous.get("declaration_identity")})
        return "wait_check_error"


def run_lifecycle(capsule_path, directory, *, turn_runner=host_turn):
    capsule_path, directory = Path(capsule_path).absolute(), Path(directory).absolute()
    capsule = validate_capsule(read(capsule_path))
    directory.mkdir(parents=True, exist_ok=True)
    with _consumer_lock(directory / "lifecycle"):
        saved = directory / "capsule.json"
        if saved.exists() and read(saved) != capsule:
            raise EvidenceError("immutable host capsule changed; use a separately authorized run")
        if not saved.exists():
            save(saved, capsule)
            save(directory / "started.json", {"started_at": stamp(), "adapter": {"path": str(Path(__file__).absolute()), **file_identity(__file__)}})
        save(directory / "checkpoint-schema.json", CHECKPOINT_SCHEMA)
        receipts, session_id = [], None
        bounds = capsule["bounds"]
        began = datetime.fromisoformat(read(directory / "started.json")["started_at"])
        for number in range(1, bounds["max_host_turns"] + 1):
            folder = directory / ("turn-%03d" % number)
            previous_session = session_id
            if (folder / "receipt.json").exists():
                receipt = read(folder / "receipt.json")
            elif folder.exists():
                # Recover host judgment only after positive exit evidence; no
                # scientific command is replayed, even when its outcome is lost.
                record = read(folder / "process.json") if (folder / "process.json").exists() else {}
                observation = inspect_host_process(record)
                try:
                    receipt = reconcile_exited_host(capsule, folder, session_id, observation, record)
                except (OSError, ValueError, KeyError, EvidenceError) as exc:
                    observation["reconciliation_error"] = str(exc)[:500]
                    receipt = None
                if receipt is None:
                    return summarize(capsule, directory, receipts,
                        "host_still_running" if observation["state"] == "running" else "unknown_host_outcome",
                        {**record, "observation": observation})
            else:
                elapsed = (datetime.now(timezone.utc) - began).total_seconds()
                if elapsed >= bounds["max_wall_seconds"]:
                    return summarize(capsule, directory, receipts, "wall_admission_bound")
                declared_wait = receipts and (directory / ("turn-%03d" % len(receipts)) / "wait.json").exists()
                previous_checkpoint = receipts[-1]["checkpoint"] if receipts and not receipts[-1].get("reconciliation_required") else None
                if previous_checkpoint and previous_checkpoint["state"] == "waiting" and declared_wait:
                    reason = await_completion(capsule, directory, receipts, began)
                    if reason:
                        return summarize(capsule, directory, receipts, reason)
                elif previous_checkpoint and previous_checkpoint["wake_after_seconds"]:
                    due = datetime.fromisoformat(receipts[-1]["finished_at"]).timestamp() + receipts[-1]["checkpoint"]["wake_after_seconds"]
                    while due > time.time():
                        remaining = bounds["max_wall_seconds"] - (datetime.now(timezone.utc) - began).total_seconds()
                        if remaining <= 0:
                            return summarize(capsule, directory, receipts, "wall_admission_bound")
                        time.sleep(min(due - time.time(), remaining, 30))
                if (datetime.now(timezone.utc) - began).total_seconds() >= bounds["max_wall_seconds"]:
                    return summarize(capsule, directory, receipts, "wall_admission_bound")
                selected_session = session_id if capsule.get("session_mode", "resume") == "resume" else None
                receipt = turn_runner(capsule, folder, selected_session)
            receipt = continue_recorded_interruption(capsule, folder, previous_session, receipt)
            receipts.append(receipt)
            session_id = receipt["session_id"]
            if receipt.get("reconciliation_required"):
                summarize(capsule, directory, receipts, "host_exit_reconciliation_pending")
                continue
            if receipt["exit_code"] != 0 or not session_id or not receipt["session_continuity_verified"] or receipt["checkpoint"] is None:
                return summarize(capsule, directory, receipts, "host_error_or_invalid_checkpoint")
            state = receipt["checkpoint"]["state"]
            if state in ("complete", "needs_user"):
                return summarize(capsule, directory, receipts, "host_" + state)
            summarize(capsule, directory, receipts, "checkpoint_continuing")
        return summarize(capsule, directory, receipts, "host_turn_bound")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capsule")
    parser.add_argument("directory")
    parser.add_argument("--goal", help="Natural research goal; first positional argument is an existing entry")
    parser.add_argument("--profile", type=Path, help="Existing runtime/authority profile for --goal")
    parser.add_argument("--branch", help="Select one existing research branch")
    args = parser.parse_args()
    capsule_path = args.capsule
    if args.goal:
        if args.profile is None:
            parser.error("--goal requires an explicit --profile")
        capsule_path = Path(args.directory).absolute() / "goal-capsule.json"
        prior = read(capsule_path) if capsule_path.exists() else None
        selected_branch = args.branch or (prior.get("branch_id") if prior else None)
        body = capsule_from_goal(args.goal, args.capsule, read(args.profile), branch_id=selected_branch)
        if capsule_path.exists():
            # Reentry uses its saved initial work even when publication advanced.
            for key in ("initial_work", "initial_context", "read_recipe"):
                if key in prior:
                    body[key] = prior[key]
            if prior != body:
                raise EvidenceError("goal or profile changed; use a separate run")
        else:
            capsule_path.parent.mkdir(parents=True, exist_ok=True)
            save(capsule_path, body)
    # JSON CLI transport must also survive Windows shells with a GBK codepage.
    print(json.dumps(run_lifecycle(capsule_path, args.directory), ensure_ascii=True))
