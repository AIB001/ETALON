"""Optional exact-request reuse; execution and scientific judgment stay with callers.

Receipts are ordinary files suitable for existing Artifact/MethodUse recording.
This module creates neither a research store nor an executor. A started request
without a valid terminal receipt is deliberately not retried.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from etalon.active.schema import canonical, digest


def file_identity(path):
    data = Path(path).read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def request_identity(*, operation, implementation, environment, inputs, parameters):
    """Bind all inputs, including coordinates/H atoms, model weights and options.

    Callers provide the complete dependency manifest. This helper cannot prove
    that an omitted dependency is irrelevant. Paths alone are not identities.
    """
    if not operation or not implementation or not environment or not inputs:
        raise ValueError("operation, implementation, environment and inputs are required")
    body = {"schema": "etalon-method-reuse-request/1", "operation": operation,
            "implementation": implementation, "environment": environment,
            "inputs": inputs, "parameters": parameters}
    return {**body, "request_digest": digest(body)}


def _write_new(path, body):
    # Exclusive creation prevents concurrent callers from executing one request.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(canonical(body) + "\n")


def execute_or_reuse(directory, request, execute):
    """Call execute once, or verify and return its JSON result and artifact pins.

    execute must return {result: JSON, artifacts: [absolute file paths]}. Failed
    callbacks are terminal too. Original measured cost is retained; reuse costs
    are new verification overhead, never an independent scientific replicate.
    """
    start, cpu = time.perf_counter(), time.process_time()
    body = {k: v for k, v in request.items() if k != "request_digest"}
    key = digest(body)
    if request.get("request_digest") != key:
        raise ValueError("request digest mismatch")
    root = Path(directory) / key
    root.mkdir(parents=True, exist_ok=True)
    request_path, receipt_path = root / "request.json", root / "receipt.json"
    if request_path.exists():
        if json.loads(request_path.read_text(encoding="utf-8")) != request:
            raise ValueError("request identity collision")
        if not receipt_path.exists():
            raise RuntimeError("unresolved started request; explicit recovery required")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        payload = {k: v for k, v in receipt.items() if k != "receipt_digest"}
        if receipt.get("receipt_digest") != digest(payload) or receipt["request_digest"] != key:
            raise ValueError("terminal receipt integrity mismatch")
        for artifact in receipt["artifacts"]:
            if file_identity(artifact["path"]) != artifact["identity"]:
                raise ValueError("cached artifact identity changed")
        return {"reused": True, "receipt": receipt, "receipt_path": str(receipt_path.absolute()),
                "call_cost": {"wall_seconds": time.perf_counter() - start,
                              "cpu_seconds": time.process_time() - cpu}}
    _write_new(request_path, request)
    execution_start, execution_cpu = time.perf_counter(), time.process_time()
    try:
        output = execute()
        artifacts = []
        for path in output["artifacts"]:
            path = Path(path)
            if not path.is_absolute():
                raise ValueError("artifact paths must be absolute")
            artifacts.append({"path": str(path), "identity": file_identity(path)})
        result, status, error = output["result"], "complete", None
        canonical(result)  # Fail before receipt publication for non-JSON results.
    except Exception as exc:
        result, status, error, artifacts = None, "failed", type(exc).__name__ + ": " + str(exc), []
    receipt = {"schema": "etalon-method-reuse-receipt/1", "request_digest": key,
               "status": status, "result": result, "error": error, "artifacts": artifacts,
               "execution_cost": {"wall_seconds": time.perf_counter() - execution_start,
                                  "cpu_seconds": time.process_time() - execution_cpu},
               "independent_replicate_on_reuse": False}
    receipt["receipt_digest"] = digest(receipt)
    _write_new(receipt_path, receipt)
    return {"reused": False, "receipt": receipt, "receipt_path": str(receipt_path.absolute()),
            "call_cost": {"wall_seconds": time.perf_counter() - start,
                          "cpu_seconds": time.process_time() - cpu}}
