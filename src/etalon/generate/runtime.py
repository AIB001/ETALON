"""Resolve an explicit PRISM installation into a reusable invocation, without installing it."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import argparse

from etalon.runtime.files import file_identity


_INSPECT = '''import json,sys,shutil
from pathlib import Path
import prism
from prism.generation.registry import model_capabilities
from prism.generation.runner import load_generation_config
from prism.generation import weights
r=json.load(sys.stdin)
c=load_generation_config(Path(r["config"]))
m=c["models"][r["model"]]
gaps=weights.configured_gaps(r["model"],m)
print(json.dumps({"python":sys.executable,"prism_source":str(Path(prism.__file__).parent),
 "capabilities":model_capabilities()[r["model"]],"backend":m.get("backend"),
 "environment":m.get("environment"),"command":m.get("command"),"model_commit":m.get("model_commit"),
 "conda":shutil.which("conda"),"artifacts":m.get("artifacts",{}),
 "missing_weights":[{"name":n,"path":str(p)} for n,p in gaps]}))
'''


def inspect_runtime(python, config, model, *, prism_source=None, environment=None):
    """Inspect the actual control interpreter and selected config, never a GPU job.

    The selected model may run in another environment/container. Successful
    inspection does not assert that its CUDA kernels or wrapper have run.
    """
    python, config = str(Path(python).absolute()), str(Path(config).absolute())
    environment = environment or {}
    allowed = {"PATH", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES", "PRISM_MODELS_DIR"}
    if (not isinstance(environment, dict) or set(environment) - allowed
            or any(not isinstance(v, str) for v in environment.values())):
        raise ValueError("runtime environment accepts only explicit path/device/weight locations, not credentials")
    env = os.environ.copy()
    env.update(environment)
    if prism_source is not None:
        root = Path(prism_source).absolute()
        env["PYTHONPATH"] = str(root)
    completed = subprocess.run([python, "-c", _INSPECT], input=json.dumps({"config": config, "model": model}),
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=90, check=True)
    observed = json.loads(completed.stdout)
    if prism_source is not None and Path(observed["prism_source"]).resolve() != (root / "prism").resolve():
        raise ValueError("PRISM imported from a different installation")
    pins = {"config": {"path": config, **file_identity(config)}}
    for name, artifact in observed["artifacts"].items():
        if isinstance(artifact, dict) and artifact.get("path"):
            path = Path(artifact["path"])
            if path.is_file():
                actual = file_identity(path)
                if artifact.get("sha256") and artifact["sha256"].lower() != actual["sha256"]:
                    raise ValueError("configured weight hash differs: " + name)
                pins[name] = {"path": str(path), **actual}
    for module in ("__init__.py", "generation/runner.py", "generation/execution.py"):
        path = Path(observed["prism_source"]) / module
        pins[module] = {"path": str(path), **file_identity(path)}
    for argument in observed.get("command") or []:
        path = Path(argument)
        if path.suffix in {".py", ".yaml", ".json"} and path.is_absolute() and path.is_file():
            pins["command:" + argument] = {"path": str(path), **file_identity(path)}
    return {"model": model, "command_prefix": [python, "-m", "prism.cli"],
        "source_root": str(Path(observed["prism_source"]).parent), "config": config,
        "environment": environment,
        "observed": observed, "files": pins, "readiness": "inspected_not_executed",
        "scope": "control interpreter/config/declared weights; model environment needs a real selected call"}


def generation_command(profile, *, protein, pocket, output, num_samples, seed, device="auto",
                       reference_ligand=None, pocket_kind="auto", add_hydrogens=True,
                       qc="full", resume=False):
    """Bind the host's scientific choice; returns argv/env, never starts generation."""
    if profile["observed"]["missing_weights"]:
        raise ValueError("prepare the missing model weights before generation")
    if type(num_samples) is not int or num_samples < 1 or type(seed) is not int:
        raise ValueError("num_samples must be positive and seed explicit")
    if qc not in {"off", "basic", "standard", "full"}:
        raise ValueError("unknown PRISM quality level")
    if pocket_kind not in {"auto", "structure", "reference_ligand", "center", "residues"}:
        raise ValueError("unknown PRISM pocket kind")
    if profile["observed"]["capabilities"]["requires_reference_ligand"] and not reference_ligand:
        raise ValueError("selected model requires a reference ligand")
    for key, pin in profile["files"].items():
        if file_identity(pin["path"]) != {k: pin[k] for k in ("sha256", "bytes")}:
            raise ValueError("runtime profile changed: " + key)
    command = [*profile["command_prefix"], "generate", "--model", profile["model"],
        "--generation-config", profile["config"], "--protein", str(Path(protein).absolute()),
        "--pocket", str(Path(pocket).absolute()), "--pocket-kind", pocket_kind,
        "--output", str(Path(output).absolute()),
        "--num-samples", str(num_samples), "--seed", str(seed), "--device", device, "--qc", qc]
    if reference_ligand:
        command.extend(["--reference-ligand", str(Path(reference_ligand).absolute())])
    if resume:
        command.append("--resume")
    if not add_hydrogens:
        command.append("--no-hydrogens")
    return {"argv": command, "env": {**profile.get("environment", {}), "PYTHONPATH": profile["source_root"]},
        "next": "execute with the existing native task handle; keep this job's configuration unchanged"}


def main():
    """A method.run adapter; PRISM keeps its native manifest and resume semantics."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    inputs = request["inputs"]
    profile = json.loads(Path(inputs["profile"]).read_text(encoding="utf-8"))
    manifest = Path(request["outputs"]["manifest"])
    invocation = generation_command(profile, output=manifest.parent, **inputs["generation"])
    env = {**os.environ, **invocation["env"]}
    subprocess.run(invocation["argv"], env=env, check=True)
    body = json.loads(manifest.read_text(encoding="utf-8"))
    args.result.write_text(json.dumps({"schema": "etalon-method-result/1",
        "summary": f"PRISM returned {body.get('status')}; {body.get('candidate_count')} candidates. Host selects next action.",
        "outputs": {"manifest": str(manifest)},
        "feedback": {"status": body.get("status"), "candidate_count": body.get("candidate_count"),
                     "quality": {k: body.get("quality", {}).get(k) for k in ("level", "pose_checks_enabled", "quarantined_count")},
                     "downstream_started": False}}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
