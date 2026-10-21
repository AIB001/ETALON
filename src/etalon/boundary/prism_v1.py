"""Independent PRISM v1 JSON boundary. Never imports PRISM or vendored asset metadata.

Local execution and POSIX SSH execution have explicit interpreter/cwd/path contracts.
The SSH host is an operator-configured SSH alias; credentials never enter requests.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from etalon.active.schema import canonical, digest, finite

SCHEMA = "etalon-prism-task/1"
IDENTITIES = ("request_id", "campaign_id", "action_id", "candidate_id", "chemical_state_id",
              "pose_id", "endpoint_id", "protocol_id")
STATES = {"queued", "running", "needs_resume", "succeeded", "failed", "invalid", "cancelled"}
TERMINAL = {"succeeded", "failed", "invalid", "cancelled"}
ENDPOINT = {"name": "mean_residue_contacts", "units": "count", "contact_cutoff_nm": 0.4,
            "scope": "engineering_only"}


class ContractError(ValueError):
    """Response cannot establish the requested scientific identity/evidence."""


class TransportError(RuntimeError):
    """Connection outcome is unknown; this is not a scientific task failure."""


class RemoteError(RuntimeError):
    def __init__(self, error: dict):
        self.error = error
        super().__init__(f"{error.get('code')}: {error.get('message')}")


def fields(value: Any, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise ContractError(f"expected fields {sorted(required)}, optional {sorted(optional)}")


def sha(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContractError("expected lowercase SHA256")


def file_identity(path: str | Path) -> dict:
    h = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return {"sha256": h.hexdigest(), "bytes": size}


def protocol_sha256(request: dict) -> str:
    return digest({"inputs": {k: v["sha256"] for k, v in request["inputs"].items()},
                   "endpoint": request["endpoint"], "workflow_spec": request["workflow_spec"],
                   "workflow": "bounded-5jwt-benzene/1"})


def validate_request(request: dict) -> None:
    fields(request, {"schema_version", *IDENTITIES, "operation", "replicate", "seed", "inputs",
                     "workflow_spec", "resources", "endpoint"})
    if request["schema_version"] != SCHEMA or request["operation"] != "new_run":
        raise ContractError("unsupported schema or operation")
    if any(not isinstance(request[k], str) or not request[k].strip() for k in IDENTITIES):
        raise ContractError("empty identity")
    if type(request["replicate"]) is not int or request["replicate"] < 0 or type(request["seed"]) is not int:
        raise ContractError("replicate and seed must be explicit integers")
    if request["endpoint"] != ENDPOINT:
        raise ContractError("unsupported engineering endpoint")
    fields(request["inputs"], {"protein", "ligand"})
    for item in request["inputs"].values():
        fields(item, {"path", "sha256"})
        if not isinstance(item["path"], str) or not item["path"]:
            raise ContractError("input requires an execution-host path")
        sha(item["sha256"])
    fields(request["resources"], {"threads", "wall_time_limit_seconds"})
    for key in request["resources"]:
        if type(request["resources"][key]) is not int or request["resources"][key] < 1:
            raise ContractError("resource limits must be positive integers")
    if request["resources"]["threads"] > 2:
        raise ContractError("round1 is limited to two threads")
    fields(request["workflow_spec"], {"md_steps", "build"})
    spec = request["workflow_spec"]
    steps = spec["md_steps"]
    if type(steps) is not int or not 100 <= steps <= 1000 or steps % 100:
        raise ContractError("bounded md_steps must be 100..1000 in steps of 100")
    fixed = {"forcefield": "amber14sb", "ligand_forcefield": "gaff2", "water_model": "tip3p",
             "protonation": "propka", "ligand_charge": 0, "dt": .001, "em_tolerance": 200,
             "temperature": 310, "ph": 7, "salt_concentration": .15, "box_distance": 1,
             "box_shape": "dodecahedron", "pressure": 1, "nterm_met": "keep"}
    fields(spec["build"], {*fixed, "production_ns", "nvt_ps", "npt_ps", "em_steps"})
    if any(spec["build"][k] != v or isinstance(spec["build"][k], bool) for k, v in fixed.items()):
        raise ContractError("unsupported bounded build settings")
    for key, low, high in (("production_ns", .0001, .001), ("nvt_ps", .1, 1), ("npt_ps", .1, 1)):
        finite(spec["build"][key], key, minimum=low)
        if spec["build"][key] > high:
            raise ContractError("bounded build duration exceeded")
    if type(spec["build"]["em_steps"]) is not int or not 1 <= spec["build"]["em_steps"] <= 20000:
        raise ContractError("bounded em_steps exceeded")
    if abs(spec["build"]["production_ns"] * 1000 - steps * spec["build"]["dt"]) > 1e-10:
        raise ContractError("production duration and md_steps disagree")
    if not 0 <= request["seed"] <= 2147483647 or request["replicate"] > 2147483647:
        raise ContractError("seed/replicate out of bounds")
    if request["resources"]["wall_time_limit_seconds"] > 3600:
        raise ContractError("wall time exceeds bounded contract")
    canonical(request)


_HASH_SCRIPT = """import hashlib,json,sys
h=hashlib.sha256(); n=0
with open(sys.argv[1],'rb') as f:
 while True:
  b=f.read(1048576)
  if not b: break
  h.update(b); n+=len(b)
print(json.dumps({'sha256':h.hexdigest(),'bytes':n}))
"""
_WRITE_SCRIPT = """import os,pathlib,sys,tempfile
p=pathlib.Path(sys.argv[1]); b=sys.stdin.buffer.read(); p.parent.mkdir(parents=True,exist_ok=True)
if p.exists():
 if p.read_bytes()!=b: raise ValueError('immutable request conflict')
else:
 fd,t=tempfile.mkstemp(dir=p.parent)
 with os.fdopen(fd,'wb') as f: f.write(b); f.flush(); os.fsync(f.fileno())
 try: os.link(t,p)
 except FileExistsError:
  if p.read_bytes()!=b: raise ValueError('immutable request conflict')
 finally: os.unlink(t)
print('{}')
"""

_FINGERPRINT_SCRIPT = """import hashlib,importlib.util,json,pathlib,sys
root=pathlib.Path.cwd().resolve()/'prism'
spec=importlib.util.find_spec('prism')
if spec is None or pathlib.Path(spec.origin).resolve()!=root/'__init__.py':
 raise ValueError('PRISM module does not resolve to configured source directory')
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
 for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts
 and p.suffix in {'.py','.json','.yaml','.yml','.mdp','.itp','.rtp','.atp','.dat','.hdb','.tdb'}}
if not files or not (root/'bridge'/'__main__.py').is_file(): raise ValueError('independent bridge source missing')
print(json.dumps({'module_origin':spec.origin,'executable':sys.executable,
 'python_sha256':hashlib.sha256(pathlib.Path(sys.executable).read_bytes()).hexdigest(),'sources':files}))
"""


@dataclass(frozen=True)
class CLITransport:
    python: str
    cwd: str
    request_root: str
    host: str | None = None
    timeout: float = 60

    def __post_init__(self) -> None:
        if self.host is not None:
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", self.host):
                raise ValueError("host must be an explicit SSH alias or user@host")
            if any(not PurePosixPath(p).is_absolute() for p in (self.python, self.cwd, self.request_root)):
                raise ValueError("SSH requires absolute POSIX interpreter/cwd/request paths")
        elif any(not Path(p).is_absolute() for p in (self.python, self.cwd, self.request_root)):
            raise ValueError("local transport requires absolute interpreter/cwd/request paths")
        finite(self.timeout, "transport timeout", minimum=0)
        if self.timeout == 0:
            raise ValueError("transport timeout must be positive")

    def _run(self, argv: list[str], data: bytes | None = None) -> dict:
        if self.host:
            # SSH invokes a POSIX shell remotely. Quote every argument, including Python source.
            command = "cd " + shlex.quote(self.cwd) + " && exec " + shlex.join(argv)
            args, cwd = ["ssh", "-o", "BatchMode=yes", "--", self.host, command], None
        else:
            args, cwd = argv, self.cwd
        try:
            proc = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=cwd, timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TransportError(str(error)) from error
        try:
            response = json.loads(proc.stdout)
            if not isinstance(response, dict):
                raise ValueError("response is not an object")
        except (ValueError, UnicodeError) as error:
            raise TransportError(f"non-JSON CLI response, exit={proc.returncode}") from error
        if "error" in response and (proc.returncode or "status" not in response):
            raise RemoteError(response["error"])
        if proc.returncode:
            raise TransportError(f"CLI exit={proc.returncode} without structured error")
        return response

    def call(self, operation: str, **arguments: str) -> dict:
        if operation not in {"capabilities", "submit", "status", "result", "resume"}:
            raise ValueError("unsupported bridge operation")
        argv = [self.python, "-m", "prism.bridge", operation]
        for key, value in arguments.items():
            argv.extend(["--" + key, value])
        return self._run(argv)

    def stage_request(self, request: dict) -> str:
        validate_request(request)
        cls = PurePosixPath if self.host else Path
        path = str(cls(self.request_root) / (digest(request) + ".json"))
        self._run([self.python, "-c", _WRITE_SCRIPT, path], canonical(request).encode())
        return path

    def identity(self, path: str) -> dict:
        if self.host:
            if not PurePosixPath(path).is_absolute():
                raise ContractError("remote artifact must have an absolute POSIX path")
            return self._run([self.python, "-c", _HASH_SCRIPT, path])
        if not Path(path).is_absolute():
            raise ContractError("local artifact must have an absolute path")
        return file_identity(path)

    def verify(self, artifact: dict) -> None:
        sha(artifact.get("sha256"))
        actual = self.identity(artifact["path"])
        if actual["sha256"] != artifact["sha256"] or (
                "bytes" in artifact and actual["bytes"] != artifact["bytes"]):
            raise ContractError(f"artifact identity differs: {artifact['path']}")

    def fingerprint(self) -> dict:
        """Read source/interpreter hashes without importing PRISM or starting science."""
        return self._run([self.python, "-c", _FINGERPRINT_SCRIPT])

    def map_input(self, source: str | Path, execution_path: str) -> dict:
        """Explicit pre-staged path mapping; refuses mismatched or absent remote bytes.

        Upload is deliberately operator-owned. No local path is implicitly reinterpreted on SSH.
        """
        artifact = {"path": execution_path, **file_identity(source)}
        self.verify(artifact)
        return {key: artifact[key] for key in ("path", "sha256")}


def validate_identity(request: dict, response: dict) -> None:
    for key in ("schema_version", *IDENTITIES, "seed", "replicate"):
        if response.get(key) != request[key]:
            raise ContractError(f"response identity mismatch: {key}")
    if response.get("request_sha256") != digest(request):
        raise ContractError("request hash mismatch")
    if response.get("protocol_sha256") != protocol_sha256(request):
        raise ContractError("protocol hash mismatch")
    if response.get("status") not in STATES:
        raise ContractError("unknown job state")
    if not isinstance(response.get("job_id"), str) or not response["job_id"]:
        raise ContractError("job identity missing")


def execution_records(result: dict) -> list[dict]:
    cost = result.get("cost", {})
    records = cost.get("executions")
    if not isinstance(records, list):
        raise ContractError("execution cost records missing")
    if type(cost.get("threads")) is not int or not 1 <= cost["threads"] <= 2:
        raise ContractError("execution threads missing")
    if type(cost.get("compute_executed")) is not bool or type(cost.get("reused")) is not bool:
        raise ContractError("execution cost provenance missing")
    if any(r.get("finished") is None for r in records):
        raise ContractError("unfinished execution cost is not a measured final charge")
    return [{**r, "threads": cost["threads"]} for r in records]


def execution_cost(result: dict) -> float:
    """Fixed mapping: one wall_second per measured execution wall second, never CPU seconds."""
    records = execution_records(result)
    seen, total = set(), 0.0
    for record in records:
        key = record.get("execution_id")
        if not isinstance(key, str) or not key or key in seen:
            raise ContractError("missing or duplicate execution identity")
        seen.add(key)
        finite(record.get("wall_seconds"), "execution wall seconds", minimum=0)
        if type(record.get("threads")) is not int or record["threads"] < 1:
            raise ContractError("execution threads missing")
        total += record["wall_seconds"]
    finite(total, "total execution cost", minimum=0)
    finite(result["cost"].get("wall_seconds"), "total wall_seconds", minimum=0)
    if not math.isclose(total, result["cost"]["wall_seconds"], rel_tol=1e-12, abs_tol=1e-9):
        raise ContractError("execution total disagrees with cost")
    if records and result.get("execution_id") != records[-1]["execution_id"]:
        raise ContractError("latest execution identity mismatch")
    return total


def validate_result(request: dict, result: dict, transport: CLITransport) -> None:
    validate_identity(request, result)
    if result["status"] not in TERMINAL:
        raise ContractError("nonterminal result cannot be admitted")
    execution_cost(result)
    if result.get("scope") != "engineering_only":
        raise ContractError("result scope mismatch")
    if result["status"] != "succeeded":
        if result.get("observation") is not None:
            raise ContractError("failed task cannot supply a numeric observation")
        return
    obs = result.get("observation", {})
    if obs.get("name") != ENDPOINT["name"] or obs.get("units") != "count":
        raise ContractError("observation endpoint mismatch")
    finite(obs.get("value"), "contact count", minimum=0)
    if obs.get("uncertainty") is not None:
        finite(obs["uncertainty"], "uncertainty", minimum=0)
    if type(obs.get("n_frames")) is not int or obs["n_frames"] < 1:
        raise ContractError("missing production frames")
    times = obs.get("time_range_ps")
    if not isinstance(times, list) or len(times) != 2 or any(
            type(t) not in (float, int) or not math.isfinite(t) for t in times) or times[1] < times[0]:
        raise ContractError("invalid production time range")
    if not isinstance(obs.get("definition"), dict) or not isinstance(obs.get("checks"), dict):
        raise ContractError("definition and checks required")
    definition, checks = obs["definition"], obs["checks"]
    if (definition.get("contact_cutoff_nm") != .4 or definition.get("comparison") != "<"
            or definition.get("pbc") != "periodic minimum image"
            or not definition.get("ligand_selection") or not definition.get("protein_selection")
            or not definition.get("residue_identity") or not definition.get("averaging")):
        raise ContractError("contact definition mismatch")
    if any(checks.get(k) is not True for k in ("manifest_verified", "frame_coverage",
            "aggregate_agreement", "zero_contact_frames_included", "production_grid_verified")):
        raise ContractError("contact evidence checks failed")
    context = result.get("provenance", {}).get("context", {})
    if any(not context.get(k) for k in ("sources", "software", "tools", "executable")):
        raise ContractError("actual software/source and run/analysis provenance required")
    manifest = result.get("artifacts")
    if not isinstance(manifest, dict) or not manifest.get("inputs") or not manifest.get("outputs"):
        raise ContractError("artifact manifest required")
    artifacts = manifest["inputs"] + manifest["outputs"]
    for artifact in artifacts:
        if type(artifact.get("bytes")) is not int or artifact["bytes"] < 0:
            raise ContractError("artifact byte count required")
        transport.verify(artifact)
    for item in request["inputs"].values():
        if not any(a["path"] == item["path"] and a["sha256"] == item["sha256"] for a in artifacts):
            raise ContractError("input missing from artifact manifest")
    for filename in ("frame_by_frame_contacts.csv", "contact_proportions.csv"):
        if not any(PurePosixPath(a["path"].replace("\\", "/")).name == filename for a in artifacts):
            raise ContractError(f"missing raw contact artifact {filename}")
