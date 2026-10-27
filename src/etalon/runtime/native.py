"""Opt-in native execution handoff and recovery; no scheduler or automatic retry.

The host explicitly launches one immutable request/attempt. A detached worker
waits for its native command and saves an exit/output receipt even if the host
controller disappears. Ambiguous outcomes remain unknown and never re-launch.
Journal ingestion and resource settlement stay with the existing host APIs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from etalon.active.schema import canonical, digest
from .files import _consumer_lock
from .files import EvidenceError, file_identity
from .files import _copy, _read, _save

INTENT = "etalon-native-attempt-intent/1"
FINISHED = "etalon-native-attempt-finished/1"
RECEIPT = "etalon-native-attempt-receipt/1"

# A small descriptor beside the existing entry; no second job store. Transport
# is explicit, and the only remote operation is this package's read-only reader.
NATIVE_HANDLE = "etalon-native-handle/1"


def validate_native_handle(handle):
    from .files import _sha
    if (not isinstance(handle, dict) or set(handle) != {
            "schema", "backend", "request_id", "descriptor", "identity"}
            or handle.get("schema") != NATIVE_HANDLE or handle.get("backend") != "prism"):
        raise EvidenceError("unsupported native handle")
    if not isinstance(handle["request_id"], str) or not handle["request_id"]:
        raise EvidenceError("native request id required")
    if not isinstance(handle["descriptor"], str) or not Path(handle["descriptor"]).is_absolute():
        raise EvidenceError("native descriptor must be an absolute path")
    identity = handle["identity"]
    if not isinstance(identity, dict) or set(identity) != {"sha256", "bytes"}:
        raise EvidenceError("native descriptor identity required")
    _sha(identity["sha256"])
    if type(identity["bytes"]) is not int or not 0 < identity["bytes"] <= 1024 * 1024:
        raise EvidenceError("native descriptor exceeds bound")
    return handle


def bind_native_handle(directory, *, native, profile, transport, reader_files):
    """Bind an already-created PRISM job and fixed profile, without dispatch.

    reader_files names deployed copies of boundary/native_result.py and
    boundary/runtime_profile.py (path/sha256/bytes). A local or SSH transport is
    selected explicitly; neither discovery nor a default environment is used.
    """
    from etalon.boundary import native_result, runtime_profile
    # Paths belong to the target OS; validate its manifest here, full spec there.
    if (profile.get("schema") != runtime_profile.PROFILE_SCHEMA
            or profile.get("manifest_digest") != digest({k: v for k, v in profile.items() if k != "manifest_digest"})):
        raise EvidenceError("fixed runtime manifest digest mismatch")
    if native.get("schema") != "prism-gromacs-job-handle/1" or native.get("python") != profile["spec"]["python"]:
        raise EvidenceError("native handle requires its exact fixed runtime")
    if native.get("source") not in profile["spec"]["pythonpath"]:
        raise EvidenceError("native source absent from fixed runtime")
    if set(reader_files) != {"native_result", "runtime_profile"}:
        raise EvidenceError("both fixed reader modules required")
    for name, module in (("native_result", native_result), ("runtime_profile", runtime_profile)):
        item = reader_files[name]
        if set(item) != {"path", "sha256", "bytes"} or {k: item[k] for k in ("sha256", "bytes")} != file_identity(module.__file__):
            raise EvidenceError("deployed reader must match this package")
    if transport != {"kind": "local"}:
        import re
        if (set(transport) != {"kind", "host"} or transport["kind"] != "ssh"
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,253}", transport["host"])):
            raise EvidenceError("explicit local transport or SSH alias required")
    descriptor = {"schema": "etalon-native-descriptor/1", "native": native,
                  "profile": profile, "transport": transport, "reader_files": reader_files}
    root = Path(directory).absolute()
    root.mkdir(parents=True, exist_ok=True)
    path = root / (digest(descriptor) + ".json")
    _save(path, descriptor)
    return validate_native_handle({"schema": NATIVE_HANDLE, "backend": "prism",
        "request_id": native["request_id"], "descriptor": str(path), "identity": file_identity(path)})


# Fixed bootstrap, never an argv or shell command taken from research prose.
# Modules are checked before import, including before runtime profile checks.
_NATIVE_READER = '''import hashlib,importlib.util,json,pathlib,sys
d=json.load(sys.stdin)
modules={}
for name in ('runtime_profile','native_result'):
 p=d['reader_files'][name]; data=pathlib.Path(p['path']).read_bytes()
 if len(data)!=p['bytes'] or hashlib.sha256(data).hexdigest()!=p['sha256']: raise ValueError('reader source changed')
for name in ('runtime_profile','native_result'):
 p=d['reader_files'][name]
 s=importlib.util.spec_from_file_location(name,p['path']); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); modules[name]=m
r=modules['native_result'].read_prism_result(d['native'],d['profile'],modules['runtime_profile'])
print(json.dumps(r,allow_nan=False))
'''


def _transport_environment():
    """Windows OpenSSH needs CommonAppData even in a minimal MCP environment."""
    environment = os.environ.copy()
    if os.name == "nt" and "PROGRAMDATA" not in environment:
        import ctypes
        directory = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0023, None, 0, directory) != 0:
            raise EvidenceError("Windows CommonAppData unavailable for SSH transport")
        environment["PROGRAMDATA"] = directory.value
    return environment


def read_native_result(handle, *, timeout=60):
    """Inspect the original handle and verify bytes. Never resume native work."""
    import shlex
    validate_native_handle(handle)
    path = Path(handle["descriptor"])
    if file_identity(path) != handle["identity"]:
        raise EvidenceError("native descriptor bytes changed")
    descriptor = _read(path)
    if descriptor["native"]["request_id"] != handle["request_id"]:
        raise EvidenceError("native descriptor/request mismatch")
    argv = [descriptor["native"]["python"], "-P", "-B", "-c", _NATIVE_READER]
    transport = descriptor["transport"]
    if transport["kind"] == "ssh":
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", transport["host"], shlex.join(argv)]
    elif transport != {"kind": "local"}:
        raise EvidenceError("unsupported native transport")
    start = time.monotonic()
    try:
        completed = subprocess.run(argv, input=canonical(descriptor), capture_output=True,
            text=True, encoding="utf-8", timeout=timeout, check=False, env=_transport_environment())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("native read unavailable: " + type(exc).__name__) from exc
    if completed.returncode:
        raise EvidenceError(f"native read failed (exit {completed.returncode}): "
                            + (completed.stderr or completed.stdout)[-1200:])
    result = json.loads(completed.stdout)
    if (result.get("schema") != "etalon-native-action-result/1"
            or result.get("request_id") != handle["request_id"]
            or result.get("profile_digest") != descriptor["profile"]["manifest_digest"]):
        raise EvidenceError("native reader returned a different request/profile")
    if file_identity(path) != handle["identity"]:
        raise EvidenceError("native descriptor changed during read")
    result["read_cost"] = {"wall_seconds": time.monotonic() - start,
                           "scope": "transport_profile_checks_and_native_original_verification"}
    return result


def process_identity(pid):
    """Read a native creation identity. An absent/unreadable PID returns None.

    Linux binds boot ID and /proc start ticks, retaining observed command bytes.
    An exec chain can change command bytes without changing process creation.
    Windows
    binds kernel creation FILETIME and the actual executable image. PID alone,
    mtime, and a successful signal probe are never accepted as identity.
    """
    if type(pid) is not int or pid < 1:
        raise EvidenceError("positive native PID required")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                   wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            length = wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(length.value)
            code = wintypes.DWORD()
            if (not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times])
                    or not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length))
                    or not kernel.GetExitCodeProcess(handle, ctypes.byref(code))):
                return None
            return {"pid": pid, "platform": "windows", "creation_filetime":
                    (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime,
                    "image": image.value, "alive": code.value == 259}
        finally:
            kernel.CloseHandle(handle)
    try:
        proc = Path("/proc") / str(pid)
        # A command can contain spaces or parentheses; fields begin after the
        # final ')' of comm. starttime is field 22, hence index 19 here.
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        command = (proc / "cmdline").read_bytes()
        return {"pid": pid, "platform": "linux", "boot_id":
                Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "start_ticks": int(fields[19]), "cmdline_sha256": hashlib.sha256(command).hexdigest(),
                "alive": fields[0] not in {"Z", "X"}}
    except (OSError, ValueError, IndexError):
        return None


def _same_process(saved):
    current = process_identity(saved["pid"])
    keys = ("pid", "platform", "boot_id", "start_ticks") if saved["platform"] == "linux" else (
        "pid", "platform", "creation_filetime")
    return (current is not None and current.get("alive") is True
            and all(current.get(k) == saved.get(k) for k in keys))


def _intent(directory):
    root = Path(directory).absolute()
    body = _read(root / "intent.json")
    if body.get("schema") != INTENT or body.get("directory") != str(root):
        raise EvidenceError("native attempt intent schema/directory mismatch")
    return root, body, digest(body)


def _check_finished(root, intent, identity, *, verify_outputs=True):
    body = _read(root / "finished.json")
    if (body.get("schema") != FINISHED or body.get("intent_digest") != identity
            or body.get("request_id") != intent["request"]["request_id"]
            or body.get("attempt_id") != intent["request"]["attempt_id"]
            or body.get("launch_nonce") != intent.get("launch_nonce")):
        raise EvidenceError("native completion differs from immutable attempt")
    for name in ("native", "worker"):
        record = _read(root / (name + ".json"))
        if (body.get(name) != record.get("process") or record.get("intent_digest") != identity
                or record.get("launch_nonce") != intent.get("launch_nonce")):
            raise EvidenceError("native completion process/request identity mismatch")
    expected = {"stdout": str(root / "stdout.log"), "stderr": str(root / "stderr.log"),
                **intent["outputs"]}
    files = body.get("files", {})
    missing = body.get("missing_outputs", [])
    if set(files) & set(missing) or set(files) | set(missing) != set(expected):
        raise EvidenceError("native completion output inventory mismatch")
    for name, spec in files.items():
        if spec.get("path") != expected[name] or verify_outputs and file_identity(spec["path"]) != {
                k: spec[k] for k in ("sha256", "bytes")}:
            raise EvidenceError("native output bytes changed")
    return body


def _check_receipt(root, identity):
    _, intent, _ = _intent(root)
    receipt = _read(root / "receipt.json")
    if (receipt.get("schema") != RECEIPT or receipt.get("intent_digest") != identity
            or receipt.get("launch_nonce") != intent.get("launch_nonce")
            or receipt.get("request_id") != intent["request"]["request_id"]
            or receipt.get("attempt_id") != intent["request"]["attempt_id"]):
        raise EvidenceError("native receipt differs from immutable attempt")
    if receipt.get("finished_identity") != file_identity(root / "finished.json"):
        raise EvidenceError("native completion receipt bytes changed")
    finished = _check_finished(root, intent, identity, verify_outputs=False)
    if (receipt.get("finished") != finished or set(receipt.get("archives", {})) != set(finished["files"])
            or receipt.get("execution") != ("completed" if finished["returncode"] == 0
                                          and not finished["missing_outputs"] else "failed")):
        raise EvidenceError("native receipt completion/output inventory changed")
    for name, spec in receipt["archives"].items():
        pin = {k: finished["files"][name][k] for k in ("sha256", "bytes")}
        if (spec != {"path": str(root / "originals" / pin["sha256"]), **pin}
                or file_identity(spec["path"]) != pin):
            raise EvidenceError("native receipt archived original changed")
    return receipt


def inspect_attempt(directory):
    """Read current evidence; running / finished-unpublished / published / unknown.

    Unknown includes a dead worker without completion, a PID reuse, or the
    durable-intent/start window. This API never starts or retries a command.
    """
    root, intent, identity = _intent(directory)
    base = {"schema": "etalon-native-attempt-status/1", "directory": str(root),
            "request_id": intent["request"]["request_id"], "attempt_id": intent["request"]["attempt_id"],
            "intent_digest": identity, "automatic_retry": False}
    if (root / "receipt.json").exists():
        return {**base, "state": "published", "receipt": _check_receipt(root, identity)}
    if (root / "finished.json").exists():
        return {**base, "state": "finished-unpublished", "finished": _check_finished(root, intent, identity)}
    live = []
    for name in ("worker", "native"):
        path = root / (name + ".json")
        if path.exists():
            record = _read(path)
            if record.get("intent_digest") != identity or record.get("launch_nonce") != intent.get("launch_nonce"):
                raise EvidenceError("native process handle differs from attempt")
            if _same_process(record["process"]):
                live.append(name)
    return {**base, "state": "running" if live else "unknown", "live_processes": live}


def launch_detached_attempt(directory, request, command, *, cwd=None, outputs=None):
    """Explicit one-time launch. Repeated entry only inspects the same attempt.

    Outputs is a bounded name -> absolute file path mapping supplied before
    execution. The caller owns scientific input/protocol bindings in request.
    A new directory/attempt is required for an explicitly authorized repeat.
    """
    if (not isinstance(request, dict) or any(not isinstance(request.get(k), str) or not request[k].strip()
            for k in ("request_id", "attempt_id")) or len(canonical(request).encode()) > 1024 * 1024):
        raise EvidenceError("bounded explicit native request/attempt required")
    if (not isinstance(command, (list, tuple)) or not command or len(command) > 256
            or any(not isinstance(v, str) or not v or len(v) > 32768 for v in command)):
        raise EvidenceError("explicit bounded native argv required")
    root = Path(directory).absolute()
    root.mkdir(parents=True, exist_ok=True)
    outputs = {} if outputs is None else outputs
    if (not isinstance(outputs, dict) or len(outputs) > 64 or set(outputs) & {"stdout", "stderr"}
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not Path(v).is_absolute()
                   for k, v in outputs.items())):
        raise EvidenceError("bounded named absolute native outputs required")
    body = {"schema": INTENT, "directory": str(root), "request": request, "command": list(command),
            "cwd": str(Path(cwd).absolute()) if cwd is not None else str(root), "outputs": outputs,
            "python": sys.executable, "module": {"path": str(Path(__file__).absolute()),
                                                  **file_identity(__file__)}}
    with _consumer_lock(root / "launch"):
        if (root / "intent.json").exists():
            existing = _read(root / "intent.json")
            if "launch_nonce" in existing:
                body["launch_nonce"] = existing["launch_nonce"]
            _save(root / "intent.json", body)
            return inspect_attempt(root)
        body["launch_nonce"] = secrets.token_hex(16)
        _save(root / "intent.json", body)
        # Intent is the no-retry boundary. If launch or worker bootstrap fails,
        # the unknown outcome remains visible instead of silently re-executing.
        options = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                             | subprocess.CREATE_BREAKAWAY_FROM_JOB}
        with (root / "worker.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-m", __name__, "_worker", str(root)],
                cwd=body["cwd"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, **options)
        # Retain the parent-side Popen while it starts; no native completion wait.
        for _ in range(100):
            if (root / "worker.json").exists() or process.poll() is not None:
                break
            time.sleep(0.01)
        return inspect_attempt(root)


def recover_attempt(directory):
    """Verify finished native bytes and idempotently publish an archival receipt.

    Running/unknown attempts are returned unchanged; recovery has no execution
    branch. Actual costs are never charged or settled here.
    """
    root, intent, identity = _intent(directory)
    with _consumer_lock(root / "publish"):
        status = inspect_attempt(root)
        if status["state"] != "finished-unpublished":
            return status
        finished = _check_finished(root, intent, identity)
        archives = {}
        for name, spec in finished["files"].items():
            pin = {k: spec[k] for k in ("sha256", "bytes")}
            target = root / "originals" / pin["sha256"]
            _copy(spec["path"], target, pin)
            archives[name] = {"path": str(target), **pin}
        receipt = {"schema": RECEIPT, "request_id": intent["request"]["request_id"],
            "attempt_id": intent["request"]["attempt_id"], "intent_digest": identity,
            "launch_nonce": intent.get("launch_nonce"),
            "finished_identity": file_identity(root / "finished.json"), "finished": finished,
            "archives": archives, "execution": "completed" if finished["returncode"] == 0
                and not finished["missing_outputs"] else "failed",
            "new_execution_by_recovery": False, "new_scientific_charge": 0, "automatic_retry": False,
            "cost_basis": "native consumption remains in original receipts; no ledger settlement"}
        _save(root / "receipt.json", receipt)
        return inspect_attempt(root)


def _worker(directory):
    root, intent, identity = _intent(directory)
    if file_identity(__file__) != {k: intent["module"][k] for k in ("sha256", "bytes")}:
        raise EvidenceError("native recovery worker source drift")
    with _consumer_lock(root / "worker"):
        if (root / "worker.json").exists():
            return  # Never relaunch even after a worker dies with unknown outcome.
        worker = process_identity(os.getpid())
        if worker is None:
            raise EvidenceError("worker native process identity unavailable")
        _save(root / "worker.json", {"intent_digest": identity, "launch_nonce": intent.get("launch_nonce"), "process": worker})
        started = time.monotonic()
        with (root / "stdout.log").open("wb") as out, (root / "stderr.log").open("wb") as err:
            native = subprocess.Popen(intent["command"], cwd=intent["cwd"], stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=err, close_fds=True)
            native_identity = process_identity(native.pid)
            if native_identity is None:
                # Do not kill or repeat an outcome we cannot safely identify.
                raise EvidenceError("native process creation identity unavailable")
            _save(root / "native.json", {"intent_digest": identity, "launch_nonce": intent.get("launch_nonce"), "process": native_identity})
            returncode = native.wait()
        files, missing = {}, []
        for name, path in {"stdout": str(root / "stdout.log"), "stderr": str(root / "stderr.log"),
                           **intent["outputs"]}.items():
            if Path(path).is_file():
                files[name] = {"path": path, **file_identity(path)}
            else:
                missing.append(name)
        _save(root / "finished.json", {"schema": FINISHED, "intent_digest": identity, "launch_nonce": intent.get("launch_nonce"),
            "request_id": intent["request"]["request_id"], "attempt_id": intent["request"]["attempt_id"],
            "worker": worker, "native": native_identity, "returncode": returncode,
            "worker_wall_second": time.monotonic() - started, "files": files,
            "missing_outputs": missing, "scientific_status": "not_inferred_from_exit_code"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["_worker", "inspect", "recover"])
    parser.add_argument("directory")
    args = parser.parse_args()
    if args.operation == "_worker":
        _worker(args.directory)
    else:
        print(canonical((inspect_attempt if args.operation == "inspect" else recover_attempt)(args.directory)))
