"""Read-only native Codex hook discovery, before spending a model turn.

Only initialize, config/read and hooks/list are sent. No thread, model request,
hook invocation, config write, trust override or trust bypass is performed.
Returned configuration is a strict metadata allowlist, never the full config.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

SCHEMA = "etalon-codex-hook-inspection/1"
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_MESSAGES = 1024
HOOK_FIELDS = ("eventName", "key", "currentHash", "trustStatus", "source", "sourcePath", "enabled", "isManaged")


def _reader(stream, messages):
    """Bound notifications/config replies without printing any raw payload."""
    try:
        total = 0
        for _ in range(MAX_MESSAGES):
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                messages.put(None)
                return
            size = len(line.encode("utf-8"))
            total += size
            if size > MAX_LINE_BYTES or total > MAX_TOTAL_BYTES:
                messages.put({"inspection_error": "app_server_message_exceeds_bound"})
                return
            try:
                value = json.loads(line)
            except ValueError:
                messages.put({"inspection_error": "invalid_app_server_json"})
                return
            messages.put(value)
        messages.put({"inspection_error": "app_server_message_count_exceeds_bound"})
    except (OSError, ValueError):
        messages.put({"inspection_error": "app_server_read_failed"})


def _request(process, messages, identifier, method, params, deadline):
    process.stdin.write(json.dumps({"id": identifier, "method": method, "params": params}) + "\n")
    process.stdin.flush()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex hook inspection timed out")
        try:
            response = messages.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError("Codex hook inspection timed out") from None
        if response is None:
            raise RuntimeError("Codex app-server closed before inspection completed")
        if not isinstance(response, dict):
            raise RuntimeError("Invalid app-server response")
        if "inspection_error" in response:
            raise RuntimeError(response["inspection_error"])
        if response.get("id") != identifier:
            continue
        if "error" in response:
            # RPC message text can include arbitrary config; expose only a code.
            error = response["error"]
            code = error.get("code") if isinstance(error, dict) else None
            raise RuntimeError(f"Codex {method} failed with RPC code {code}")
        if not isinstance(response.get("result"), dict):
            raise RuntimeError("Missing app-server result object")
        return response["result"]


def _close(process):
    """Close only this inspection subprocess; no process-name/tree termination."""
    try:
        process.stdin.close()
    except (OSError, ValueError):
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    stream = getattr(process, "stdout", None)
    if hasattr(stream, "close"):
        stream.close()


def _layer(layer):
    name = layer.get("name", {})
    if not isinstance(name, dict):
        return {"source": "unknown", "disabled_reason": None}
    # Explicit source metadata only. No layer.config or environment values.
    return {"source": name.get("type", "unknown"),
            "path": name.get("dotCodexFolder", name.get("file")),
            "disabled_reason": layer.get("disabledReason")}


def inspect(target, executable="codex", timeout=30):
    """Inspect native discovery/trust metadata without changing it.

    timeout is the overall RPC deadline (seconds), followed by at most six seconds
    of task-specific shutdown. Discovery never certifies hook delivery or model
    consumption. A missing project layer is unknown, not implicitly trusted.
    """
    target = Path(target).absolute()
    if not target.is_dir():
        raise ValueError("inspection target must be an existing directory")
    if not isinstance(executable, (str, Path)) or not str(executable):
        raise ValueError("Codex executable must be explicit")
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise ValueError("inspection timeout must be positive and at most 120 seconds")
    deadline = time.monotonic() + timeout
    process = subprocess.Popen([str(executable), "app-server", "--stdio"], cwd=str(target),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    messages = queue.Queue()
    threading.Thread(target=_reader, args=(process.stdout, messages), daemon=True).start()
    try:
        _request(process, messages, 1, "initialize", {
            "clientInfo": {"name": "etalon-hook-inspection", "version": "1"},
            "capabilities": {"experimentalApi": True}}, deadline)
        process.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        process.stdin.flush()
        config = _request(process, messages, 2, "config/read", {"cwd": str(target), "includeLayers": True}, deadline)
        hooks = _request(process, messages, 3, "hooks/list", {"cwds": [str(target)]}, deadline)
        layers = [_layer(layer) for layer in (config.get("layers") or [])]
        entries = []
        for entry in hooks.get("data", []):
            metadata = [{key: h.get(key) for key in HOOK_FIELDS} for h in entry.get("hooks", [])]
            entries.append({"cwd": entry.get("cwd"), "hooks": metadata,
                "errors": [{"path": error.get("path"), "message": str(error.get("message", ""))[:1024]}
                           for error in entry.get("errors", [])],
                "warning_count": len(entry.get("warnings", []))})
        project_layers = [layer for layer in layers if layer["source"] == "project"]
        project_status = "absent_or_not_reported" if not project_layers else "blocked" if any(
            layer["disabled_reason"] for layer in project_layers) else "loaded"
        metadata = [hook for entry in entries for hook in entry["hooks"]]
        needs_review = sum(hook["enabled"] is True and hook["trustStatus"] in {"untrusted", "modified"} for hook in metadata)
        config_body = config.get("config", {})
        features = config_body.get("features") or {}
        return {"schema": SCHEMA, "target": str(target), "layers": layers,
            "features_hooks_explicit_value": features.get("hooks"),
            "allow_managed_hooks_only_explicit_value": config_body.get("allow_managed_hooks_only"),
            "project_layer_status": project_status, "entries": entries,
            "discovered_hook_count": len(metadata), "review_required_count": needs_review,
            "discovery_only": True, "hook_execution_verified": False, "model_consumption_verified": False,
            "model_turns_started": 0, "trust_modified": False, "hook_trust_bypassed": False,
            "interpretation": "Missing metadata is not proof of trust; inspect project-layer reasons and exact hook review status."}
    finally:
        _close(process)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.target, executable=args.executable, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        # Exceptions from Popen can contain commands/paths; do not echo raw args.
        print(json.dumps({"schema": SCHEMA, "inspection_failed": True, "error_type": type(error).__name__,
                          "hook_execution_verified": False}), file=sys.stderr)
        return 1
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
