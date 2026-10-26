"""Read an existing PRISM job in its fixed runtime. No submit, signal or retry.

This stdlib-loadable adapter is deployed beside runtime_profile.py. Its caller
checks both source hashes before import; profile verification retains actual
imports and required checks. The scientific supervisor remains PRISM's.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys


def identity(path):
    h = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
            size += len(block)
    return {"sha256": h.hexdigest(), "bytes": size}


def read_prism_result(native, profile, runtime):
    """Fresh original verification; saved terminal state is not a live PID claim."""
    if native.get("schema") != "prism-gromacs-job-handle/1":
        raise ValueError("unsupported native handle")
    if profile["spec"]["python"] != native["python"]:
        raise ValueError("native interpreter differs from fixed profile")
    checked = runtime.verify(profile, "prism.inspect")
    if not checked["verified"]:
        raise ValueError("fixed native runtime changed or required check failed")
    root = Path(native["job_root"])
    contract_path = root / "contract.json"
    expected = {k: native["contract"][k] for k in ("sha256", "bytes")}
    if not root.is_absolute() or str(contract_path) != native["contract"]["path"] or identity(contract_path) != expected:
        raise ValueError("native contract identity changed")
    contract = json.loads(contract_path.read_text("utf-8"))
    if contract.get("provenance", {}).get("request_id") != native["request_id"]:
        raise ValueError("native request differs from contract")
    spec = profile["spec"]
    os.environ.update(runtime._environment(spec))
    sys.path[:0] = spec["pythonpath"]
    from prism.workflow.checkpoint import GromacsJob
    state_pin = identity(root / "state.json")
    # PRISM verifies input contract, bound supervisor and every completed output.
    state = GromacsJob(root).inspect()
    if identity(root / "state.json") != state_pin:
        raise ValueError("native state changed during read; read the same handle again")
    if state.get("contract_sha256") != expected["sha256"]:
        raise ValueError("native state/contract mismatch")
    current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    same_boot = current_boot == native["boot_id"]
    if state["status"] == "running" and not same_boot:
        raise ValueError("live process belongs to a different Linux boot")
    status = state["status"]
    execution = {"succeeded": "completed", "failed": "failed", "running": "running",
                 "ready": "pending", "paused": "unresolved", "interrupted": "unresolved"}.get(status, "unresolved")
    outputs = []
    for name, sha in sorted(state.get("outputs", {}).items()):
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("native output escaped its job directory")
        outputs.append({"path": str(path), "sha256": sha, "bytes": path.stat().st_size})
    return {"schema": "etalon-native-action-result/1", "request_id": native["request_id"],
        "execution_state": execution, "native_state": status, "attempt": state["attempt"],
        "checkpoint": state.get("checkpoint"), "state_identity": state_pin,
        "contract_identity": expected, "profile_digest": profile["manifest_digest"],
        "verification": {"state": "passed" if status == "succeeded" else "pending",
            "validator": "prism.workflow.checkpoint.GromacsJob.inspect",
            "original_outputs": "verified" if status == "succeeded" else "not_terminal",
            "profile": checked, "same_boot": same_boot},
        "outputs": outputs, "validation": state.get("validation"),
        "recovery": {"backend": "prism", "operation": "read_action_result",
                     "automatic_retry": False, "native_resume_requires_explicit_choice": True},
        "accounting_refs": [], "accounting_status": "original_receipts_not_resettled",
        "admission": "not_assessed", "scientific_success": "not_established"}
