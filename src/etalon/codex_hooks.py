"""Read-only native Codex hook discovery, before spending a model turn.

Only initialize, config/read, hooks/list and optional account/read are sent. No thread, model request,
hook invocation, config write, trust override or trust bypass is performed.
Returned configuration is a strict metadata allowlist, never the full config.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

SCHEMA = "etalon-codex-hook-inspection/1"
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_MESSAGES = 1024
MAX_HELP_BYTES = 65536
HOOK_FIELDS = ("eventName", "key", "currentHash", "trustStatus", "source", "sourcePath", "enabled", "isManaged")


class InspectionError(RuntimeError):
    """Allowlisted diagnostics; never include raw config or process output."""

    def __init__(self, code, stage, rpc_code=None):
        self.code, self.stage = code, stage
        self.rpc_code = rpc_code if type(rpc_code) is int else None
        super().__init__(f"{stage}: {code} (RPC code {self.rpc_code})")


def _select_transport(help_text):
    """Select only a stdio form advertised by this executable, not its version."""
    def option(name):
        return bool(re.search(r"(?m)^\s*(?:-[a-zA-Z],\s*)?" + re.escape(name) + r"(?:\s|=|$)", help_text))
    if option("--listen") and "stdio://" in help_text:
        return ["--listen", "stdio://"], "listen_stdio"
    if option("--stdio"):
        return ["--stdio"], "stdio_flag"
    if re.search(r"(?i)(?:default[^\n]{0,80}stdio|stdio[^\n]{0,80}default)", help_text):
        return [], "default_stdio"
    raise InspectionError("stdio_transport_not_advertised", "transport_probe")


def _probe_transport(executable, target, deadline):
    """Bound help bytes and time before launching any JSON-RPC server."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Codex transport inspection timed out")
    process = subprocess.Popen([str(executable), "app-server", "--help"], cwd=str(target),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = queue.Queue()
    def read_help():
        try:
            output.put(process.stdout.read(MAX_HELP_BYTES + 1))
        except (OSError, ValueError):
            output.put(None)
    threading.Thread(target=read_help, daemon=True).start()
    try:
        try:
            value = output.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError("Codex transport inspection timed out") from None
        if value is None:
            raise InspectionError("help_read_failed", "transport_probe")
        raw = value.encode("utf-8")
        if len(raw) > MAX_HELP_BYTES:
            raise InspectionError("help_output_exceeds_bound", "transport_probe")
        try:
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise TimeoutError("Codex transport inspection timed out") from None
        if code != 0:
            raise InspectionError("app_server_help_failed", "transport_probe")
        arguments, selected = _select_transport(value)
        return {"arguments": arguments, "selection": selected, "probe": "app-server --help",
                "help_sha256": hashlib.sha256(raw).hexdigest(), "help_bytes": len(raw)}
    finally:
        _close(process)


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
            raise InspectionError("rpc_error", method, code)
        if not isinstance(response.get("result"), dict):
            raise RuntimeError("Missing app-server result object")
        return response["result"]


def _close(process):
    """Close only this inspection subprocess; no process-name/tree termination."""
    try:
        if process.stdin is not None:
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


def inspect(target, executable="codex", timeout=30, *, check_login=False):
    """Inspect native discovery/trust metadata without changing it.

    timeout covers transport probing and RPCs, followed by bounded task-specific
    shutdown. Optional account/read disables token refresh and does not validate
    model entitlement. Discovery never certifies hook delivery or model
    consumption. A missing project layer is unknown, not implicitly trusted.
    """
    target = Path(target).absolute()
    if not target.is_dir():
        raise ValueError("inspection target must be an existing directory")
    if not isinstance(executable, (str, Path)) or not str(executable):
        raise ValueError("Codex executable must be explicit")
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise ValueError("inspection timeout must be positive and at most 120 seconds")
    if type(check_login) is not bool:
        raise ValueError("check_login must be a boolean")
    deadline = time.monotonic() + timeout
    transport = _probe_transport(executable, target, deadline)
    process = subprocess.Popen([str(executable), "app-server", *transport["arguments"]], cwd=str(target),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    messages = queue.Queue()
    threading.Thread(target=_reader, args=(process.stdout, messages), daemon=True).start()
    try:
        _request(process, messages, 1, "initialize", {
            "clientInfo": {"name": "etalon-hook-inspection", "title": "ETALON hook inspection", "version": "1"},
            "capabilities": {"experimentalApi": True}}, deadline)
        process.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        process.stdin.flush()
        config = _request(process, messages, 2, "config/read", {"cwd": str(target), "includeLayers": True}, deadline)
        diagnostics = []
        try:
            hooks = _request(process, messages, 3, "hooks/list", {"cwds": [str(target)]}, deadline)
            discovery_status = "checked"
        except InspectionError as error:
            discovery_status = "unsupported" if error.rpc_code == -32601 else "failed"
            diagnostics.append({"stage": error.stage, "code": error.code, "rpc_code": error.rpc_code})
            hooks = {}
        login = {"status": "not_checked"}
        if check_login:
            try:
                account = _request(process, messages, 4, "account/read", {"refreshToken": False}, deadline)
                present = isinstance(account.get("account"), dict)
                required = account.get("requiresOpenaiAuth")
                kind = account["account"].get("type") if present else None
                login = {"status": "credential_present_unvalidated" if present else
                    "missing" if required is True else "provider_auth_not_required" if required is False else "unknown",
                    "requires_openai_auth": required if type(required) is bool else None,
                    "account_type": kind if kind in {"apiKey", "chatgpt", "chatgptAuthTokens", "amazonBedrock"} else None,
                    "token_refresh_requested": False, "model_access_verified": False}
            except InspectionError as error:
                login = {"status": "unsupported" if error.rpc_code == -32601 else "failed"}
                diagnostics.append({"stage": error.stage, "code": error.code, "rpc_code": error.rpc_code})
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
        review = "required" if needs_review else "unknown"
        if project_status == "blocked":
            review = "project_blocked"
        elif discovery_status == "checked" and metadata and all(
                hook["enabled"] is False or hook["trustStatus"] == "trusted" for hook in metadata):
            review = "no_pending_reviews_reported"
        return {"schema": SCHEMA, "target": str(target), "layers": layers,
            "transport_probe": transport, "app_server_transport": transport["selection"],
            "login": login, "diagnostics": diagnostics,
            "verification_stages": {"static_installation": "not_checked", "app_server": "reachable",
                "native_discovery": discovery_status, "host_review": review, "login": login["status"],
                "mcp_reachability": "not_checked", "native_event_acceptance": "not_verified",
                "model_consumption": "not_verified"},
            "features_hooks_explicit_value": features.get("hooks"),
            "allow_managed_hooks_only_explicit_value": config_body.get("allow_managed_hooks_only"),
            "project_layer_status": project_status, "entries": entries,
            "discovered_hook_count": len(metadata), "review_required_count": needs_review,
            "discovery_only": True, "hook_execution_verified": False, "model_consumption_verified": False,
            "acceptance_complete": False,
            "model_turns_started": 0, "trust_modified": False, "hook_trust_bypassed": False,
            "interpretation": "Missing metadata is not proof of trust; inspect project-layer reasons and exact hook review status."}
    finally:
        _close(process)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--check-login", action="store_true", help="Read account metadata without refreshing tokens")
    args = parser.parse_args(argv)
    try:
        result = inspect(args.target, executable=args.executable, timeout=args.timeout, check_login=args.check_login)
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        # Exceptions from Popen can contain commands/paths; do not echo raw args.
        diagnostic = {"schema": SCHEMA, "inspection_failed": True, "error_type": type(error).__name__,
                      "hook_execution_verified": False, "model_consumption_verified": False}
        if isinstance(error, InspectionError):
            diagnostic.update(stage=error.stage, code=error.code, rpc_code=error.rpc_code)
        print(json.dumps(diagnostic), file=sys.stderr)
        return 1
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
