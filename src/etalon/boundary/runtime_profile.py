"""Explicit, content-bound Python launches; no environment discovery or scheduler.

A profile proves only the imported files, declared assets and checks it names. It
does not freeze every lazily imported dependency, install an environment, select a
default, or turn an environment check into scientific validation. Profiles can be
captured and verified using the standard library alone, including as this file's
standalone CLI when the target environment has an older ETALON installed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SPEC_SCHEMA = "etalon-runtime-spec/1"
PROFILE_SCHEMA = "etalon-runtime-profile/1"
BINDING_SCHEMA = "etalon-runtime-binding/1"
_MARKER = "ETALON_RUNTIME_PROBE_JSON="
_ENV_KEYS = {
    "PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "GMXLIB", "RDBASE",
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "CUDA_VISIBLE_DEVICES", "TORCH_HOME", "HF_HOME",
}
_INFLUENCE_KEYS = _ENV_KEYS | {"PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE",
                               "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class RuntimeProfileError(RuntimeError):
    """A fixed runtime is unavailable, changed, unverified or incorrectly specified."""


def _canonical(value: object) -> str:
    # Same canonical JSON convention as etalon.active.schema, without requiring the
    # controller's ETALON install to be importable in the interpreter being probed.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _fields(value: object, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise ValueError(f"expected fields {sorted(required)} with optional {sorted(optional or set())}")


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(f"{name} must be nonempty text without NUL characters")
    return value


def _name(value: object) -> str:
    value = _string(value, "identifier")
    if not _NAME.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def _absolute(value: object) -> str:
    value = _string(value, "path")
    if not Path(value).is_absolute() or "\n" in value or "\r" in value:
        raise ValueError(f"an explicit absolute path is required: {value}")
    # Never resolve an interpreter symlink. Invoking its base target loses the venv.
    return value


def _argv(value: object, *, program: bool = False) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("argv must be a nonempty list")
    result = [_string(v, "argv item") for v in value]
    if program and result[0] != "{python}":
        _absolute(result[0])
    return result


def _seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 3600:
        raise ValueError("check timeout must be finite, positive and at most 3600 seconds")
    return float(value)


def _spec(value: dict) -> dict:
    _fields(value, {"schema", "python", "cwd", "modules", "checks", "capabilities"},
            {"pythonpath", "environment", "environment_root", "files", "probe_timeout_seconds"})
    if value["schema"] != SPEC_SCHEMA:
        raise ValueError(f"runtime spec schema must be {SPEC_SCHEMA}")
    result = json.loads(_canonical(value))
    result["python"], result["cwd"] = _absolute(value["python"]), _absolute(value["cwd"])
    paths = value.get("pythonpath", [])
    if not isinstance(paths, list):
        raise ValueError("pythonpath must be a list of explicit absolute paths")
    result["pythonpath"] = [_absolute(p) for p in paths]
    result["environment_root"] = _absolute(value.get("environment_root", str(Path(result["python"]).parent.parent)))
    environment = value.get("environment", {})
    if not isinstance(environment, dict) or environment.keys() - _ENV_KEYS:
        raise ValueError("environment accepts only documented path/thread/device keys; do not store secrets")
    result["environment"] = {k: _string(v, "environment value") for k, v in environment.items()}
    # A fixed default avoids an ambient PATH or user PYTHONPATH becoming a fallback.
    result["environment"].setdefault("PATH", os.defpath)
    result["probe_timeout_seconds"] = _seconds(value.get("probe_timeout_seconds", 120))
    modules, files, checks = {}, {}, {}
    for key in ("modules", "checks"):
        if not isinstance(value[key], list):
            raise ValueError(f"{key} must be a list")
    for module in value["modules"]:
        _fields(module, {"name"}, {"loader", "expected_root", "asset_dir"})
        name = _name(module["name"])
        if name in modules or any(not p.isidentifier() for p in name.split(".")):
            raise ValueError("module names must be unique dotted Python identifiers")
        loader = module.get("loader", "import")
        if loader not in {"import", "asset"}:
            raise ValueError("module loader must be import or asset")
        modules[name] = {"name": name, "loader": loader}
        if "expected_root" in module:
            modules[name]["expected_root"] = _absolute(module["expected_root"])
        if "asset_dir" in module:
            if loader != "asset":
                raise ValueError("asset_dir requires the existing infra asset loader")
            modules[name]["asset_dir"] = _absolute(module["asset_dir"])
    if not isinstance(value.get("files", []), list):
        raise ValueError("files must be a list")
    for item in value.get("files", []):
        _fields(item, {"id", "path", "kind"})
        key = _name(item["id"])
        if key in files or item["kind"] not in {"native", "weight", "source", "data"}:
            raise ValueError("file IDs must be unique and kind native/weight/source/data")
        files[key] = {**item, "path": _absolute(item["path"])}
    for check in value["checks"]:
        _fields(check, {"id", "argv"}, {"timeout_seconds"})
        key = _name(check["id"])
        if key in checks:
            raise ValueError("check IDs must be unique")
        checks[key] = {"id": key, "argv": _argv(check["argv"], program=True),
                       "timeout_seconds": _seconds(check.get("timeout_seconds", 30))}
    if not isinstance(value["capabilities"], dict) or not value["capabilities"]:
        raise ValueError("at least one explicit capability is required")
    for name, requirements in value["capabilities"].items():
        _name(name)
        _fields(requirements, {"modules", "files", "checks"})
        for key, available in (("modules", modules), ("files", files), ("checks", checks)):
            refs = requirements[key]
            if not isinstance(refs, list) or len(set(refs)) != len(refs) or any(ref not in available for ref in refs):
                raise ValueError(f"capability {name}: {key} must name unique declared entries")
        if not requirements["checks"]:
            raise ValueError("every capability must declare at least one required verifier check")
    result["modules"], result["files"], result["checks"] = list(modules.values()), list(files.values()), list(checks.values())
    return result


def _environment(spec: dict) -> dict[str, str]:
    # Credentials and ordinary application variables may remain inherited; they
    # are never serialized. Import/library lookup variables are explicit instead.
    result = {k: v for k, v in os.environ.items() if k not in _INFLUENCE_KEYS}
    result.update(spec["environment"])
    result["PYTHONPATH"] = os.pathsep.join(spec["pythonpath"])
    result["PYTHONNOUSERSITE"] = "1"
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    return result


def _file(path: str) -> dict:
    result = {"path": path, "available": False}
    try:
        target = Path(path)
        if target.is_symlink():
            result["symlink_target"] = os.readlink(target)
        result["resolved_path"] = str(target.resolve(strict=True))
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result.update(available=True, bytes=target.stat().st_size, sha256=digest.hexdigest())
    except OSError as error:
        result["error"] = type(error).__name__
    return result


def _within(path: str, root: str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return True


def _probe(spec: dict) -> dict:
    """Runs only in the named interpreter, not in the controller's import context."""
    modules = {}
    for item in spec["modules"]:
        name = item["name"]
        try:
            provenance = None
            manifest = None
            if item["loader"] == "asset":
                boundary = importlib.import_module("etalon.boundary.infra")
                asset_dir = Path(item["asset_dir"]) if "asset_dir" in item else boundary.asset_directory()
                infra = boundary.load(name, asset_dir=asset_dir)
                module, provenance = infra.module, infra.provenance()
                manifest = _file(str(asset_dir / "MANIFEST.json"))
            else:
                module = importlib.import_module(name)
            path = getattr(module, "__file__", None)
            record = {"available": True, "loaded_from": path,
                      "version": str(getattr(module, "__version__", "unknown")),
                      "origin": getattr(getattr(module, "__spec__", None), "origin", None)}
            if provenance is not None:
                record["infra_provenance"] = provenance
                record["asset_manifest"] = manifest
            if "expected_root" in item and (not path or not _within(path, item["expected_root"])):
                record.update(available=False, error="loaded_outside_expected_root")
            modules[name] = record
        except Exception as error:  # Module failure must remain data, never a waiver.
            modules[name] = {"available": False, "error": type(error).__name__}
    loaded, bytecode = {}, {}
    by_path = {}
    for name, module in sorted(sys.modules.copy().items()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        path = os.path.abspath(path)
        if path not in by_path:
            by_path[path] = _file(path)
        loaded[name] = by_path[path]
        cached = getattr(module, "__cached__", None)
        if cached and Path(cached).is_file():
            # -B forbids writes, but Python may still read existing bytecode.
            # Record present candidates without claiming which loader branch ran.
            cached = os.path.abspath(cached)
            if cached not in by_path:
                by_path[cached] = _file(cached)
            bytecode[name] = by_path[cached]
    external = sorted({record["resolved_path"] for name, record in loaded.items()
                       if record["available"] and not _within(record["path"], spec["environment_root"])
                       and ("site-packages" in Path(record["path"]).parts
                            or any(name == item["name"] or name.startswith(item["name"] + ".") for item in spec["modules"]))})
    config = Path(sys.prefix) / "pyvenv.cfg"
    return {"python": {"requested_executable": spec["python"],
                       "reported_executable": sys.executable,
                       "requested_path_used": os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(os.path.abspath(spec["python"])),
                       "version": sys.version, "prefix": sys.prefix, "base_prefix": sys.base_prefix,
                       "executable": _file(spec["python"]),
                       "venv_config": _file(str(config)) if config.is_file() else None},
            "modules": modules, "loaded_modules": loaded, "present_bytecode_files": bytecode,
            "files": {item["id"]: {"kind": item["kind"], **_file(item["path"])} for item in spec["files"]},
            "check_programs": {check["id"]: _file(spec["python"] if check["argv"][0] == "{python}" else check["argv"][0]) for check in spec["checks"]},
            "dependency_layout": {"classification": "overlay_observed" if external else "no_overlay_observed",
                                  "external_observed_files": external,
                                  "self_contained_environment": "not_established"}}


def _inventory(spec: dict) -> dict:
    executable = _file(spec["python"])
    if not executable["available"]:
        raise RuntimeProfileError(f"explicit Python is unavailable: {spec['python']}")
    try:
        completed = subprocess.run(  # noqa: S603 -- explicit executable, no shell
            [spec["python"], "-P", "-B", str(Path(__file__).absolute()), "_probe"],
            input=_canonical(spec), capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=spec["cwd"], env=_environment(spec), timeout=spec["probe_timeout_seconds"], check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeProfileError(f"explicit Python probe failed: {type(error).__name__}") from error
    lines = [line[len(_MARKER):] for line in completed.stdout.splitlines() if line.startswith(_MARKER)]
    if completed.returncode or len(lines) != 1:
        raise RuntimeProfileError(f"explicit Python probe failed (exit {completed.returncode}; valid receipts {len(lines)})")
    inventory = json.loads(lines[0])
    if not inventory["python"]["requested_path_used"]:
        raise RuntimeProfileError("the interpreter did not retain the explicitly requested executable path")
    inventory["probe_implementation"] = _file(str(Path(__file__).absolute()))
    return inventory


def _run_checks(spec: dict, ids: set[str] | None = None) -> dict:
    receipts = {}
    for check in spec["checks"]:
        if ids is not None and check["id"] not in ids:
            continue
        argv = [spec["python"], "-P", "-B", *check["argv"][1:]] if check["argv"][0] == "{python}" else check["argv"]
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 -- explicit argv, no shell
                argv, cwd=spec["cwd"], env=_environment(spec), stdin=subprocess.DEVNULL,
                capture_output=True, timeout=check["timeout_seconds"], check=False,
            )
            receipts[check["id"]] = {"state": "passed" if completed.returncode == 0 else "failed",
                                      "returncode": completed.returncode,
                                      "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                                      "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
                                      "stdout_bytes": len(completed.stdout), "stderr_bytes": len(completed.stderr)}
        except (OSError, subprocess.TimeoutExpired) as error:
            receipts[check["id"]] = {"state": "failed", "error": type(error).__name__}
        receipts[check["id"]]["wall_seconds"] = time.monotonic() - started
    return receipts


def _capabilities(spec: dict, inventory: dict, checks: dict) -> dict:
    result = {}
    for name, requirements in spec["capabilities"].items():
        missing = [f"module:{key}" for key in requirements["modules"] if not inventory["modules"][key]["available"]]
        missing += [f"file:{key}" for key in requirements["files"] if not inventory["files"][key]["available"]]
        missing += [f"check_program:{key}" for key in requirements["checks"] if not inventory["check_programs"][key]["available"]]
        unverified = [key for key in requirements["checks"] if checks.get(key, {}).get("state") != "passed"]
        result[name] = {"available": not missing, "verified": not missing and not unverified,
                        "state": "unavailable" if missing else "available" if unverified else "verified",
                        "missing": missing, "unverified_checks": unverified,
                        "real_run": "not_established"}
    return result


def capture(spec: dict, *, run_checks: bool = True) -> dict:
    """Capture actual imports and explicit checks in the exact named interpreter."""
    spec = _spec(spec)
    inventory = _inventory(spec)
    checks = _run_checks(spec) if run_checks else {}
    profile = {"schema": PROFILE_SCHEMA, "spec": spec, "inventory": inventory,
               "identity_digest": _digest({"spec": spec, "inventory": inventory}),
               "checks": checks, "capabilities": _capabilities(spec, inventory, checks),
               "coverage": "Actual files loaded by this probe and explicitly declared files only; lazy imports, undeclared native libraries, package trees and scientific validity are not certified."}
    return {**profile, "manifest_digest": _digest(profile)}


def _profile(profile: dict) -> dict:
    if not isinstance(profile, dict) or profile.get("schema") != PROFILE_SCHEMA:
        raise ValueError(f"profile schema must be {PROFILE_SCHEMA}")
    body = {k: v for k, v in profile.items() if k != "manifest_digest"}
    if profile.get("manifest_digest") != _digest(body):
        raise RuntimeProfileError("runtime profile manifest digest does not match its content")
    _spec(profile["spec"])
    if profile["identity_digest"] != _digest({"spec": profile["spec"], "inventory": profile["inventory"]}):
        raise RuntimeProfileError("runtime profile identity digest does not match its content")
    return profile


def _known_drift(inventory: dict) -> list[str]:
    """Check known bytes before executing the recorded interpreter or imports."""
    records = {}

    def collect(value: object) -> None:
        if isinstance(value, dict):
            if "path" in value and "available" in value:
                records[value["path"]] = {key: value[key] for key in (
                    "path", "available", "resolved_path", "symlink_target", "bytes", "sha256", "error") if key in value}
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(inventory)
    return sorted(path for path, expected in records.items() if _file(path) != expected)


def verify(profile: dict, capability: str) -> dict:
    """Freshly compare fixed identities and run every required capability check."""
    profile = _profile(profile)
    spec = profile["spec"]
    if capability not in spec["capabilities"]:
        raise ValueError("capability is not declared by this profile")
    drift = _known_drift(profile["inventory"])
    if drift:
        receipt = {"schema": "etalon-runtime-verification/1", "manifest_digest": profile["manifest_digest"],
                   "capability": capability, "identity_matches": False, "checks": {}, "verified": False,
                   "changed_known_files": drift, "current_identity_digest": None,
                   "capability_status": {"state": "changed", "available": None, "verified": False,
                                         "real_run": "not_established"}, "real_run": "not_established"}
        return {**receipt, "receipt_digest": _digest(receipt)}
    current = _inventory(spec)
    unchanged = _digest({"spec": spec, "inventory": current}) == profile["identity_digest"]
    # Do not execute potentially changed native/check binaries after detecting drift.
    checks = _run_checks(spec, set(spec["capabilities"][capability]["checks"])) if unchanged else {}
    state = _capabilities(spec, current, checks)[capability]
    receipt = {"schema": "etalon-runtime-verification/1", "manifest_digest": profile["manifest_digest"],
               "capability": capability, "identity_matches": unchanged, "checks": checks,
               "verified": unchanged and state["verified"], "capability_status": state,
               "current_identity_digest": _digest({"spec": spec, "inventory": current}),
               "real_run": "not_established"}
    return {**receipt, "receipt_digest": _digest(receipt)}


def bind(profile: dict, capability: str, argv: list[str], *, task_id: str,
         command_files: list[str] | None = None) -> dict:
    """Freeze an entire profile and exact Python arguments for one existing/new task.

    Binding and launch never consult a mutable default. Callers must declare every
    script/config/input whose bytes affect their entry; direct script entries are
    added automatically. The caller's scientific input contract remains separate.
    """
    profile = json.loads(_canonical(_profile(profile)))
    argv = _argv(argv)
    if argv[0].startswith("-") and (argv[0] not in {"-c", "-m"} or len(argv) < 2):
        raise ValueError("entry must be -c CODE, -m MODULE, or an absolute script; import-mode overrides are forbidden")
    if argv[0] == "-m" and argv[1] not in {item["name"] for item in profile["spec"]["modules"]}:
        raise ValueError("a -m entry module must be explicitly captured by the profile")
    receipt = verify(profile, capability)
    if not receipt["verified"]:
        raise RuntimeProfileError("required runtime identity/check verification failed; binding refused")
    paths = {_absolute(path) for path in command_files or []}
    if not argv[0].startswith("-"):
        paths.add(_absolute(argv[0]))
    files = {path: _file(path) for path in sorted(paths)}
    if any(not item["available"] for item in files.values()):
        raise RuntimeProfileError("a declared command file is unavailable")
    binding = {"schema": BINDING_SCHEMA, "task_id": _name(task_id), "profile": profile,
               "manifest_digest": profile["manifest_digest"], "capability": capability,
               "argv": argv, "command_files": files, "verification": receipt}
    return {**binding, "binding_digest": _digest(binding)}


def launch(binding: dict, *, timeout: float | None = None) -> dict:
    """Verify a frozen binding and launch its exact Python, with no fallback.

    Child stdout/stderr remain attached to the caller. An exit code is an execution
    fact, not output admission or scientific success. No retry or recovery occurs.
    """
    if not isinstance(binding, dict) or binding.get("schema") != BINDING_SCHEMA:
        raise ValueError(f"binding schema must be {BINDING_SCHEMA}")
    if binding.get("binding_digest") != _digest({k: v for k, v in binding.items() if k != "binding_digest"}):
        raise RuntimeProfileError("runtime binding digest does not match its content")
    profile = _profile(binding["profile"])
    if binding["manifest_digest"] != profile["manifest_digest"]:
        raise RuntimeProfileError("binding names a different profile digest")
    receipt = verify(profile, binding["capability"])
    if not receipt["verified"]:
        raise RuntimeProfileError("required runtime identity/check verification failed; launch refused")
    for path, expected in binding["command_files"].items():
        if _file(path) != expected:
            raise RuntimeProfileError(f"bound command file changed: {path}")
    spec = profile["spec"]
    argv = [spec["python"], "-P", "-B", *binding["argv"]]
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 -- immutable explicit argv, no shell
            argv, cwd=spec["cwd"], env=_environment(spec), stdin=subprocess.DEVNULL,
            timeout=timeout, check=False,
        )
        result = {"state": "completed", "returncode": completed.returncode}
    except subprocess.TimeoutExpired as error:
        result = {"state": "unresolved", "returncode": None, "error": type(error).__name__}
    except OSError as error:
        result = {"state": "failed", "returncode": None, "error": type(error).__name__}
    result.update(schema="etalon-runtime-launch/1", task_id=binding["task_id"],
                  binding_digest=binding["binding_digest"], manifest_digest=profile["manifest_digest"],
                  capability=binding["capability"], argv=argv, verification=receipt,
                  wall_seconds=time.monotonic() - started, scientific_success="not_established",
                  automatic_retry=False)
    return {**result, "receipt_digest": _digest(result)}


def _read(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_new(path: str, value: dict) -> None:
    # Exclusive creation also rejects a pre-existing symlink, without replacing
    # an old binding/receipt or changing a currently executing task's profile.
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Standalone CLI, useful before a new runtime module has been installed."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["_probe"]:
        spec = _spec(json.load(sys.stdin))
        print(_MARKER + _canonical(_probe(spec)))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--spec", required=True)
    capture_parser.add_argument("--output", required=True)
    capture_parser.add_argument("--no-checks", action="store_true")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--profile", required=True)
    verify_parser.add_argument("--capability", required=True)
    verify_parser.add_argument("--output", required=True)
    bind_parser = sub.add_parser("bind")
    bind_parser.add_argument("--profile", required=True)
    bind_parser.add_argument("--capability", required=True)
    bind_parser.add_argument("--task-id", required=True)
    bind_parser.add_argument("--command-file", action="append", default=[])
    bind_parser.add_argument("--output", required=True)
    bind_parser.add_argument("argv", nargs=argparse.REMAINDER)
    launch_parser = sub.add_parser("launch")
    launch_parser.add_argument("--binding", required=True)
    launch_parser.add_argument("--receipt", required=True)
    launch_parser.add_argument("--timeout", type=float)
    args = parser.parse_args(arguments)
    try:
        if args.command == "capture":
            value = capture(_read(args.spec), run_checks=not args.no_checks)
            _write_new(args.output, value)
            return 0
        if args.command == "verify":
            value = verify(_read(args.profile), args.capability)
            _write_new(args.output, value)
            return 0 if value["verified"] else 2
        if args.command == "bind":
            command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            value = bind(_read(args.profile), args.capability, command, task_id=args.task_id,
                         command_files=args.command_file)
            _write_new(args.output, value)
            return 0
        # Check receipt writability before executing, so an existing receipt cannot
        # trigger a duplicate command before exclusive creation reports the conflict.
        with Path(args.receipt).open("x", encoding="utf-8") as receipt_stream:
            try:
                value = launch(_read(args.binding), timeout=args.timeout)
            except (ValueError, OSError, RuntimeProfileError) as error:
                receipt_stream.write(json.dumps({"schema": "etalon-runtime-launch/1", "state": "refused",
                                                 "error": str(error), "scientific_success": "not_established"}) + "\n")
                raise
            receipt_stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        return 0 if value["returncode"] == 0 else 2
    except (ValueError, OSError, RuntimeProfileError) as error:
        parser.exit(2, f"runtime profile: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BINDING_SCHEMA", "PROFILE_SCHEMA", "SPEC_SCHEMA", "RuntimeProfileError",
           "bind", "capture", "launch", "verify"]
