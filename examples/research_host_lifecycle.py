"""Optional bounded native Codex host lifecycle; scientific choices stay in the host.

The task capsule is the independent session's only task-specific prompt. Each
normal host exit leaves a compact checkpoint, then the same persisted session
can resume. External jobs belong to existing ETALON/PRISM handles, not this
adapter. No execution retry, scientific router, job database, or new allocator.

Usage: python examples/research_host_lifecycle.py CAPSULE.json RUN_DIRECTORY
The wall bound admits new host turns; it never kills an in-flight host or job.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from etalon.host import _consumer_lock
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.execution_recovery import process_identity, _same_process

CAPSULE = "etalon-host-task-capsule/1"
CHECKPOINT = "etalon-host-checkpoint/1"
STATES = ("continue", "waiting", "complete", "needs_user")
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
    native = body.get("native", {})
    if not isinstance(native, dict):
        raise EvidenceError("explicit native runtime profile required")
    for name in ("codex_executable", "cwd", "model", "reasoning_effort"):
        if not isinstance(native.get(name), str) or not native[name].strip():
            raise EvidenceError("explicit native " + name + " required")
    for name in ("codex_executable", "cwd"):
        if not Path(native[name]).is_absolute():
            raise EvidenceError("native " + name + " must be absolute")
    bounds = body.get("bounds", {})
    for name in ("max_host_turns", "max_wall_seconds"):
        if type(bounds.get(name)) is not int or bounds[name] <= 0:
            raise EvidenceError("positive integer " + name + " required")
    if len(json.dumps(body).encode("utf-8")) > 65536:
        raise EvidenceError("task capsule exceeds compact 64 KiB bound")
    if body.get("session_mode", "resume") not in {"resume", "fresh"}:
        raise EvidenceError("host session mode must be resume or fresh")
    return body


def capsule_from_goal(goal, entry, profile, *, branch_id=None, work=None):
    """Reuse a runtime/authority profile and an existing entry, without a manual capsule.

    The profile supplies genuine authority, resources and native configuration;
    natural language never manufactures those fields. A published work object is
    enough for fresh hosts; original evidence remains available through its refs.
    """
    from etalon.research.research_segment import read_work, work_from_entry, validate_work
    entry = str(Path(entry).absolute())
    if not isinstance(goal, str) or not goal.strip():
        raise EvidenceError("natural research goal required")
    if work is None:
        try:
            work = read_work(entry, branch_id=branch_id)["work"]
        except EvidenceError as exc:
            if "exactly one work object" not in str(exc):
                raise
            work = work_from_entry(goal, entry, branch_id=branch_id)
    validate_work(work)
    capsule = {"schema": CAPSULE, "task_id": work["task_id"], "entry": entry,
        "question": goal, "scope": profile.get("scope", goal),
        "authority": profile.get("authority"), "budget": profile.get("budget"),
        "completion_condition": profile.get("completion_condition", goal),
        "host_instructions": profile.get("host_instructions", "Use the current work object; batch known operations until new evidence requires judgment."),
        "native": profile.get("native"), "bounds": profile.get("bounds"),
        "session_mode": profile.get("session_mode", "resume"), "initial_work": work}
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
    return argv + ["-m", native["model"], "-c", "model_reasoning_effort=" + json.dumps(native["reasoning_effort"]),
        "-c", 'approval_policy="never"', "-c", 'sandbox_mode="danger-full-access"',
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
when the capsule completion condition is actually met; continue for unfinished authorized
work, waiting for pending work, or needs_user for a concrete dependency on new authority.
This JSON controls only host wakeup, never scientific acceptance. Keep tool output compact.
Use basis_refs for concrete entry/artifact paths or evidence identifiers. Do not edit the
capsule or adapter receipts. No full parent conversation has been injected into this session.
"""
    if session_id:
        boundary += "Your previous native host process exited. Resume this SAME saved session and reread the current entry; do not repeat completed work.\n"
    context = dict(capsule)
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
    observed_session = session_id
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
                usages.append(event.get("usage"))
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
        "session_continuity_verified": not session_id or observed_session == session_id,
        "exit_code": returncode, "usage": usages, "event_counts": event_counts,
        "checkpoint": checkpoint, "checkpoint_error": checkpoint_error,
        "checkpoint_identity": file_identity(folder / "checkpoint.json") if (folder / "checkpoint.json").exists() else None,
        "artifacts": {name: str(folder / name) for name in ("events.jsonl", "stderr.txt", "prompt.txt", "checkpoint.json")},
        "scientific_completion_inferred_from_exit": False}
    save(folder / "receipt.json", receipt)
    return receipt


def summarize(capsule, directory, receipts, stop_reason, active=None):
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    usage = {key: 0 for key in keys}
    unknown = 0
    for item in receipts:
        if not item["usage"]:
            unknown += 1
        for turn in item["usage"]:
            if not isinstance(turn, dict) or any(type(turn.get(key)) is not int for key in keys):
                unknown += 1
                continue
            for key in keys:
                usage[key] += turn[key]
    usage["uncached_input_tokens"] = usage["input_tokens"] - usage["cached_input_tokens"]
    usage["unknown_usage_records_or_turns"] = unknown
    checkpoint = receipts[-1]["checkpoint"] if receipts else None
    began = datetime.fromisoformat(read(directory / "started.json")["started_at"])
    result = {"schema": "etalon-host-lifecycle-receipt/1", "task_id": capsule["task_id"],
        "entry": capsule["entry"], "capsule_identity": file_identity(directory / "capsule.json"),
        "session_id": receipts[-1]["session_id"] if receipts else None,
        "host_turns": len(receipts), "host_wall_seconds": sum(item["wall_seconds"] for item in receipts),
        "prompt_bytes": sum(item.get("prompt_bytes", 0) for item in receipts),
        "session_mode": capsule.get("session_mode", "resume"),
        "lifecycle_elapsed_seconds": (datetime.now(timezone.utc) - began).total_seconds(),
        "elapsed_basis": "includes host processes, controller work, waits and any interval before controller reentry",
        "usage": usage, "stop_reason": stop_reason, "last_checkpoint": checkpoint,
        "active_process": active, "updated_at": stamp(),
        "turn_receipts": [str(directory / ("turn-%03d" % n) / "receipt.json") for n in range(1, len(receipts) + 1)],
        "external_compute_terminated": False, "wall_bound": "new_turn_admission_only; never kills in-flight work"}
    save(directory / "receipt.json", result)
    return result


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
            if (folder / "receipt.json").exists():
                receipt = read(folder / "receipt.json")
            elif folder.exists():
                # Crash or another controller's live host is never a retry cue.
                record = read(folder / "process.json") if (folder / "process.json").exists() else {}
                identity = record.get("identity")
                alive = identity is not None and _same_process(identity)
                return summarize(capsule, directory, receipts, "host_still_running" if alive else "unknown_host_outcome", record)
            else:
                elapsed = (datetime.now(timezone.utc) - began).total_seconds()
                if elapsed >= bounds["max_wall_seconds"]:
                    return summarize(capsule, directory, receipts, "wall_admission_bound")
                if receipts and receipts[-1]["checkpoint"]["wake_after_seconds"]:
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
            receipts.append(receipt)
            session_id = receipt["session_id"]
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
        body = capsule_from_goal(args.goal, args.capsule, read(args.profile), branch_id=args.branch)
        if capsule_path.exists():
            prior = read(capsule_path)
            # Reentry uses its saved initial work even when publication advanced.
            body["initial_work"] = prior["initial_work"]
            if prior != body:
                raise EvidenceError("goal or profile changed; use a separate run")
        else:
            capsule_path.parent.mkdir(parents=True, exist_ok=True)
            save(capsule_path, body)
    # JSON CLI transport must also survive Windows shells with a GBK codepage.
    print(json.dumps(run_lifecycle(capsule_path, args.directory), ensure_ascii=True))
