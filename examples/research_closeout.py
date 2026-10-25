"""Publish scoped, producer-verified research progress through existing APIs.

This example consumes explicit files, never scans job directories or runs science.
An ordinary project checkpoint indexes its branches and optional historical entry
locators. Producers own completion/QC; the host supplies conclusions and limits.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research import ResearchJournal
from etalon.research.components import record_json
from etalon.research.continuation import CHECKPOINT, STEP, read_content, record_content
from etalon.research.entry import resolve_entry
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.publication import _save, prepare_publication, resume_publication


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def bootstrap(root, config_path):
    """Create one scoped entry, with explicit links to separately scoped history."""
    root = Path(root).absolute()
    config_path = Path(config_path).absolute()
    config = read(config_path)
    config_pin = file_identity(config_path)
    receipt = root / "bootstrap-receipt.json"
    if receipt.exists():
        saved = read(receipt)
        if saved["config_identity"] != config_pin:
            raise EvidenceError("bootstrap scope changed; use a new explicit task")
        for pin in saved["files"]:
            if file_identity(pin["path"]) != {k: pin[k] for k in ("sha256", "bytes")}:
                raise EvidenceError("bootstrap archival bytes changed")
        return saved
    root.mkdir(parents=True, exist_ok=True)
    # Bind the scope before any journal or snapshot can survive a crash. A
    # partial archive must never acquire a different configuration on retry.
    intent = root / "bootstrap-request.json"
    binding = {"config_path": str(config_path), "config_identity": config_pin}
    if intent.exists():
        if read(intent) != binding:
            raise EvidenceError("bootstrap scope changed during interrupted creation")
    elif any((root / name).exists() for name in ("research.sqlite", "snapshot.sqlite", "entry.json")):
        raise EvidenceError("unbound partial bootstrap; preserve it and use a new explicit task")
    else:
        _save(intent, binding)
    if any(b["state"] not in {"waiting", "stopped"} for b in config["branches"]):
        raise EvidenceError("bootstrap branches must start waiting or explicitly stopped")
    task, sid = config["task_id"], config["source_id"]
    store = CampaignStore(root / "research.sqlite")
    store.configure(CampaignSpec("archive", 0, "wall_second", task),
                    [Endpoint("archive", task, "archive", "count", task, 0, queryable=False)])
    journal = ResearchJournal(store)

    def obj(value, kind):
        rid = record_json(journal, value, root / "content", source_kind="existing_real_artifact",
                          bindings={"task_id": task, "kind": kind})
        return make_ref(sid, rid, journal.get(rid))

    def content(value):
        rid = record_content(journal, value, root / "content", source_id=sid,
                             source_kind="existing_real_artifact")
        return make_ref(sid, rid, journal.get(rid))

    scope = obj(config, "explicit_task_scope")
    counters = obj({"limitations": config["limitations"], "r4b": "paused",
        "meaning": "Runtime pending handles are not a global external-job inventory. Reads never launch work."},
        "counterevidence")
    history = []
    for path in config.get("history_entries", []):
        ws, cp, *_ = resolve_entry(path)
        old = read_content(ws, cp)
        history.append({"entry": str(Path(path).absolute()), "entry_identity": file_identity(path),
            "workspace": str(ws), "workspace_identity": file_identity(ws), "checkpoint_ref": cp,
            "task_id": old["task_id"], "question": old["question"],
            "meaning": "Separate scoped historical task; its stopped/paused state does not describe this project"})
    links = obj({"history_entries": history, "frozen_at": config["as_of"],
                 "refresh": "Resolve an explicitly selected entry again to detect later publication"}, "history_navigation")
    steps, branches = [], []
    for branch in config["branches"]:
        step = {"schema": STEP, "task_id": task, "question": config["question"],
            "branch_id": branch["branch_id"], "operation": "decision", "reason": branch["reason"],
            "input_refs": [scope, links, counters], "output_refs": [], "method_refs": [],
            "accounting_refs": [], "source_watermarks": [], "supersedes": [],
            "detail": {"choice": "stop" if branch["state"] == "stopped" else "defer",
                "alternatives": [], "basis_refs": [scope, counters], "reconsider_when": branch["reconsider_when"]}}
        ref = content(step)
        steps.append(ref)
        branches.append({"branch_id": branch["branch_id"], "state": branch["state"],
            "basis_refs": [ref, scope, counters], "next_read_refs": [links, scope, counters],
            "reconsider_when": branch["reconsider_when"]})
    authority = root / "AUTHORITY.json"
    _save(authority, {"state": "active", "scope": config["question"], "r4b": "paused",
        "automatic_scientific_retry": False, "meaning": "Archival continuation only; existing scientific protocols retain their own authorization"})
    cp = content({"schema": CHECKPOINT, "task_id": task, "question": config["question"],
        "step_refs": steps, "claim_refs": [], "counter_refs": [counters], "method_refs": [],
        "branches": branches, "pending_handles": [], "source_watermarks": [], "supersedes": [],
        "status_guards": [{"location": str(authority), **file_identity(authority), "state": "active"}]})
    snapshot = root / "snapshot.sqlite"
    if not snapshot.exists():
        with store.connection() as src, sqlite3.connect(snapshot) as dst:
            src.backup(dst)
    workspace = root / "workspace.json"
    _save(workspace, {"schema": "etalon-evidence-workspace/1", "workspace_id": task,
        "sources": [{"source_id": sid, "kind": "journal", "location": str(snapshot),
            "identity": {"kind": "snapshot_sha256", "value": file_identity(snapshot)["sha256"]}}],
        "status_refs": [str(authority)], "location_map": []})
    parent = root / "initial-entry.json"
    _save(parent, {"workspace": str(workspace), "checkpoint_ref": cp})
    entry = root / "entry.json"
    if not entry.exists():
        _save(entry, read(parent))
    # This also detects an incomplete/corrupt pre-existing snapshot before a
    # success receipt can be written.
    read_content(workspace, cp)
    files = [intent, snapshot, workspace, parent, authority, *sorted((root / "content").glob("*.json"))]
    result = {"entry": str(entry), "initial_checkpoint_ref": cp, "config_identity": config_pin,
              "files": [{"path": str(p), **file_identity(p)} for p in files]}
    _save(receipt, result)
    return result


def prepare_packet(entry, packet_path):
    """Bind explicit producer artifacts and an as-of state to a recoverable update.

    Artifact-byte verification is mechanical. Scientific meaning, including any
    independent quality claim, remains the producer/host's explicit statement.
    """
    entry, packet_path = Path(entry).absolute(), Path(packet_path).absolute()
    packet, packet_pin = read(packet_path), file_identity(packet_path)
    required = {"schema", "task_id", "branch_id", "as_of", "scope", "node_state", "capability_id",
                "reason", "reconsider_when", "branch_state", "choice", "files"}
    if set(packet) != required or packet["schema"] != "etalon-closeout-packet/1":
        raise EvidenceError("invalid explicit closeout packet")
    if not isinstance(packet["as_of"], str) or not packet["as_of"] or not packet["scope"]:
        raise EvidenceError("progress needs an explicit observation time and scope")
    files = packet["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= 28:
        raise EvidenceError("closeout packet requires 1..28 explicitly named artifact files")
    categories = {"protocol", "result", "receipt", "counterevidence", "accounting", "manifest"}
    names, groups = set(), {k: [] for k in categories}
    results = []
    for item in files:
        if set(item) != {"name", "path", "sha256", "bytes", "category"} or item["category"] not in categories:
            raise EvidenceError("invalid closeout artifact binding")
        if item["name"] in names or item["name"] == "progress_packet":
            raise EvidenceError("duplicate closeout artifact name")
        names.add(item["name"])
        path = Path(item["path"])
        if not path.is_absolute() or file_identity(path) != {k: item[k] for k in ("sha256", "bytes")}:
            raise EvidenceError("closeout producer bytes changed")
        results.append({"name": item["name"], "path": str(path),
            "identity": {k: item[k] for k in ("sha256", "bytes")}, "source_kind": "existing_real_artifact",
            "role": "diagnostic", "bindings": {"task_id": packet["task_id"], "branch_id": packet["branch_id"],
                "kind": item["category"], "observed_at": packet["as_of"], "scope": packet["scope"]}})
        groups[item["category"]].append({"publication_ref": item["name"]})
    if any(not groups[k] for k in ("protocol", "result", "receipt", "counterevidence")):
        raise EvidenceError("publication needs protocol, result, receipt and explicit counterevidence")
    operation = "closeout-" + packet_pin["sha256"]
    saved = entry.parent / "requests" / (operation + ".json")
    if saved.exists():
        prior = read(saved)
        if prior["packet_identity"] != packet_pin or prior["request"]["entry"] != str(entry):
            raise EvidenceError("closeout operation identity conflict")
        return prepare_publication(**prior["request"])
    workspace, cp_ref, *_ = resolve_entry(entry)
    cp = read_content(workspace, cp_ref)
    if cp["task_id"] != packet["task_id"]:
        raise EvidenceError("closeout cannot change task scope")
    branches = {b["branch_id"]: b for b in cp["branches"]}
    if packet["branch_id"] not in branches:
        raise EvidenceError("closeout branch not present in selected checkpoint")
    results.append({"name": "progress_packet", "path": str(packet_path), "identity": packet_pin,
        "source_kind": "existing_real_artifact", "role": "diagnostic",
        "bindings": {"task_id": packet["task_id"], "kind": "as_of_progress_not_live_heartbeat"}})
    # Assemble the ordinary shape; publication resolves named refs and invokes
    # node_step's validator before any archival write.
    node = {"schema": STEP, "task_id": cp["task_id"], "question": cp["question"],
        "branch_id": packet["branch_id"], "operation": "node", "reason": packet["reason"],
        "input_refs": groups["protocol"], "output_refs": groups["result"] + groups["manifest"],
        "method_refs": [], "accounting_refs": groups["accounting"], "source_watermarks": [], "supersedes": [],
        "detail": {"node_id": operation, "workflow_ref": None, "capability_id": packet["capability_id"],
            "state": packet["node_state"], "use_refs": [], "receipt_refs": groups["receipt"],
            "identities": [], "action_handles": []}}
    basis = [{"publication_ref": "progress_packet"}, *groups["result"], *groups["counterevidence"], *groups["receipt"]]
    decision = {"schema": STEP, "task_id": cp["task_id"], "question": cp["question"],
        "branch_id": packet["branch_id"], "operation": "decision", "reason": packet["reason"],
        "input_refs": [{"publication_ref": "node"}, *basis], "output_refs": [{"publication_ref": "progress_packet"}],
        "method_refs": [], "accounting_refs": groups["accounting"], "source_watermarks": [], "supersedes": [],
        "detail": {"choice": packet["choice"], "alternatives": [], "basis_refs": basis,
            "reconsider_when": packet["reconsider_when"]}}
    update = {"branch_id": packet["branch_id"], "expected_state": branches[packet["branch_id"]]["state"],
        "state": packet["branch_state"], "basis_refs": basis, "next_read_refs": basis,
        "reconsider_when": packet["reconsider_when"], "decision_ref": {"publication_ref": "decision"}}
    request = {"entry": str(entry), "operation_id": operation, "expected_entry_identity": file_identity(entry),
        "results": results, "steps": [{"name": "node", "content": node}, {"name": "decision", "content": decision}],
        "checkpoint_additions": {"counter_refs": groups["counterevidence"]}, "branch_updates": [update]}
    _save(saved, {"packet_identity": packet_pin, "request": request})
    return prepare_publication(**request)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["bootstrap", "prepare", "resume"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    if args.operation == "bootstrap":
        result = bootstrap(args.path, args.input)
    elif args.operation == "prepare":
        result = prepare_packet(args.path, args.input)
    else:
        result = resume_publication(args.path)
    print(json.dumps(result, ensure_ascii=False))
