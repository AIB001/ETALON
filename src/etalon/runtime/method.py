"""Host-selected programs in the existing action/workflow lifecycle.

Programs receive --request and --result paths. They own algorithms and batching;
this adapter owns only the native handle, structured return and recovery.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from etalon.active.schema import canonical, digest
from etalon.active.store import StateError
from etalon.runtime import native
from etalon.runtime.files import _save, file_identity
from etalon.runtime.schema import absolute, fields, text

RESULT = "etalon-method-result/1"


def validate(parameters):
    text(parameters["method_id"], "method_id")
    command = parameters["command"]
    if (not isinstance(command, list) or not 1 <= len(command) <= 250
            or any(not isinstance(v, str) or not v for v in command)):
        raise ValueError("method command must be an explicit argv list")
    absolute(command[0])  # Keep the venv executable spelling, not its realpath.
    if not isinstance(parameters["inputs"], dict):
        raise ValueError("method inputs must be a JSON object")
    files = parameters["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("declare the method's source/model files")
    for path in files:
        absolute(path)
    if "cwd" in parameters:
        absolute(parameters["cwd"])
    outputs = parameters.get("outputs", {})
    if not isinstance(outputs, dict) or len(outputs) > 60:
        raise ValueError("method outputs must be a bounded name to relative path mapping")
    for name, value in outputs.items():
        text(name, "output name")
        if (name in {"result", "stdout", "stderr"} or not isinstance(value, str)
                or not value or not Path(value).parts or Path(value).is_absolute() or ".." in Path(value).parts
                or Path(value).parts[0] in {"native", "request.json", "result.json"}):
            raise ValueError("method outputs must remain within their step directory")


def read_result(parameters, directory):
    """Recover bytes without executing; unobserved completion remains pending."""
    root = Path(directory)
    attempt = root / "native"
    if not (attempt / "intent.json").exists():
        return None
    state = native.recover_attempt(attempt)
    if state["state"] != "published":
        return None
    receipt = state["receipt"]
    intent = json.loads((attempt / "intent.json").read_text(encoding="utf-8"))
    if intent["request"].get("parameters_sha256") != digest(parameters):
        raise StateError("native method parameters differ from this node")
    pin = receipt["archives"].get("result")
    body = None
    error = None
    if receipt["execution"] == "completed" and pin:
        try:
            if pin["bytes"] > 1024 * 1024:
                raise ValueError("method result exceeds 1 MiB; put bulk data in declared outputs")
            body = json.loads(Path(pin["path"]).read_text(encoding="utf-8"))
            fields(body, {"schema", "summary", "outputs"}, {"feedback"})
            if body["schema"] != RESULT or not isinstance(body["outputs"], dict):
                raise ValueError("method result schema/outputs invalid")
            text(body["summary"], "method summary")
            if len(body["summary"]) > 4096 or not isinstance(body.get("feedback", {}), dict):
                raise ValueError("use a compact summary and structured feedback")
        except (ValueError, KeyError, TypeError) as exc:
            error = str(exc)
    else:
        error = "native method failed or omitted a declared output"
    return {"method_id": parameters["method_id"],
            "status": "completed" if error is None else "failed",
            "summary": body["summary"] if error is None else error,
            "outputs": body["outputs"] if error is None else {},
            "feedback": body.get("feedback", {}) if error is None else {},
            "artifacts": receipt["archives"], "native_directory": str(attempt),
            "receipt_identity": file_identity(attempt / "receipt.json"),
            "wall_second": receipt["finished"]["worker_wall_second"],
            "scientific_admission": "not_inferred"}


def execute(parameters, ctx):
    validate(parameters)
    request_path, result_path = ctx.root / "request.json", ctx.root / "result.json"
    outputs = {name: str(ctx.root / relative) for name, relative in parameters.get("outputs", {}).items()}
    _save(request_path, {"schema": "etalon-method-request/1", "method_id": parameters["method_id"],
                         "inputs": parameters["inputs"], "outputs": outputs})
    request = {"request_id": ctx.job, "attempt_id": ctx.node,
               "parameters_sha256": digest(parameters), "inputs": ctx.preflight["inputs"]}
    command = [*parameters["command"], "--request", str(request_path), "--result", str(result_path)]
    native.launch_detached_attempt(ctx.root / "native", request, command,
                                  cwd=parameters.get("cwd", str(ctx.root)),
                                  outputs={"result": str(result_path), **outputs})
    # No model in the execution loop. A detached native child survives a host or
    # workflow-worker exit; reconciliation reads its handle instead of rerunning.
    while True:
        ctx.checkpoint()
        result = read_result(parameters, ctx.root)
        if result is not None:
            return result
        if native.inspect_attempt(ctx.root / "native")["state"] == "unknown":
            raise StateError("native outcome unknown; reconcile the retained handle")
        time.sleep(0.25)


def verify(parameters, result):
    expected = read_result(parameters, Path(result["native_directory"]).parent)
    if expected is None or canonical(expected) != canonical(result):
        raise StateError("method result differs from its native receipt")
    if result["status"] != "completed":
        raise StateError(result["summary"])
    return {"native_receipt": result["receipt_identity"], "scientific_admission": "not_inferred"}
