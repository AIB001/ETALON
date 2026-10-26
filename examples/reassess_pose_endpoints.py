"""Versioned local endpoint reanalysis of archived poses; no docking, MD or network.

Run two unresolved imines and one explicitly configured steroid control. New
qualifications and evaluation receipts are ordinary files/Artifacts. Historical
results and their original joint unknowns are retained unchanged. A completed
identical request is verified and reused instead of evaluating twice.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
import time

from etalon.active.schema import canonical, digest
from etalon.research.evidence_refs import make_ref
from etalon.research.input_qualification import POLICY, write_input_qualification
from etalon.research.pose_verifier import installed_pose_check_contract, verify_pose_endpoints, verify_poses
from etalon.research.result_integrity import validate_endpoint_verifier
from etalon.research.schema import Artifact
from etalon.research.scientific_input import SCHEMA, identity

CASES = ("1OWE_675", "1UOU_CMU", "1M2Z_DEX")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    data = (canonical(value) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("preserve existing output: " + str(path))
    else:
        path.write_bytes(data)


def run(archive, output):
    from rdkit import Chem, rdBase
    import importlib.metadata
    started = time.perf_counter()
    archive, output = Path(archive).absolute(), Path(output).absolute()
    output.mkdir(parents=True, exist_ok=True)
    originals = {str(archive / "panel.json"): identity(archive / "panel.json")}
    panel = {case["id"]: case for case in read(archive / "panel.json")["cases"]}
    contract = installed_pose_check_contract()
    materials = {}
    for cid in CASES:
        case = panel[cid]
        result_path, verifier_path = (archive / "outputs" / cid / name for name in ("result.json", "verifier.json"))
        result, verifier = read(result_path), read(verifier_path)
        if identity(verifier_path) != {k: result["verifier"][k] for k in ("sha256", "bytes")}:
            raise ValueError("historical verifier differs from result binding")
        files = {"predicted": archive / "outputs" / cid / "empirical.sdf",
                 "reference": archive / "evaluator" / cid / "reference.sdf",
                 "receptor": archive / "inputs" / cid / "receptor.pdb"}
        pins = {name: identity(path) for name, path in files.items()}
        for name, key in (("predicted", "pool"), ("reference", "reference"), ("receptor", "receptor")):
            if pins[name] != verifier["binding"][key]:
                raise ValueError("historical original identity mismatch: " + cid + " " + name)
        if pins["reference"] != case["reference_identity"] or pins["receptor"] != case["inputs"]["receptor.pdb"]:
            raise ValueError("source panel identity mismatch")
        originals.update({str(path): identity(path) for path in (*files.values(), result_path, verifier_path)})
        materials[cid] = (files, pins, result, verifier)
    modules = {}
    for name in ("etalon.research.pose_verifier", "etalon.research.result_integrity",
                 "etalon.research.scientific_handoff", "etalon.research.input_qualification", "rdkit", "posebusters"):
        module = importlib.import_module(name)
        modules[name] = {"path": module.__file__, **identity(module.__file__)}
    request = {"schema": "etalon-pose-endpoint-reassessment-request/1", "cases": list(CASES),
               "originals": originals, "check_contract_digest": digest(contract), "modules": modules,
               "driver": {"path": __file__, **identity(__file__)}, "executable": sys.executable,
               "new_scientific_execution": "none; local verifier reanalysis only"}
    if (output / "receipt.json").exists():
        prior = read(output / "receipt.json")
        if prior["request_digest"] != digest(request):
            raise ValueError("completed reassessment uses a different request; choose a new versioned directory")
        for relative, pin in prior["outputs"].items():
            if identity(output / relative) != pin:
                raise ValueError("completed reassessment output changed")
        print(canonical({"state": "reused_verified", "request_digest": prior["request_digest"], "new_verifier_calls": 0}))
        return prior
    save(output / "request.json", request)
    save(output / "check-contract.json", contract)
    summaries = {}
    for cid, (files, pins, historical, historical_verifier) in materials.items():
        folder = output / cid
        folder.mkdir(exist_ok=True)
        records, sources = [], []
        for name in ("reference", "predicted"):
            role = "independent_validation" if name == "reference" else "diagnostic"
            artifact = Artifact(str(files[name]), **pins[name], source_kind="existing_real_artifact", role=role,
                                bindings={"source_panel": originals[str(archive / "panel.json")],
                                          "historical_verifier": originals[str(archive / "outputs" / cid / "verifier.json")]})
            records.append({"record_id": artifact.id, "record_type": "artifact", "visibility": "released", "record": artifact.as_dict()})
            sources.append({"id": name, "role": "evaluation_only" if name == "reference" else "context",
                "scope": "archived known-pocket endpoint reanalysis", "path": str(files[name]), **pins[name],
                "locator": "source panel and historical verifier exact file identity",
                "evidence_ref": make_ref(cid, artifact.id, artifact.as_dict())})
        manifest = folder / "source-manifest.json"
        save(manifest, {"schema": "etalon-artifact-manifest/1", "records": records})
        workspace = {"schema": "etalon-evidence-workspace/1", "workspace_id": cid + "-endpoint-reassessment",
            "sources": [{"source_id": cid, "kind": "artifact_manifest", "location": str(manifest),
                         "identity": {"kind": "snapshot_sha256", "value": identity(manifest)["sha256"]}}], "location_map": []}
        save(folder / "source-workspace.json", workspace)
        bundle = {"schema": SCHEMA, "task_id": cid + "-endpoint-reassessment", "scope": "archived known-pocket endpoint reanalysis",
                  "subject": {"id": cid}, "sources": sources, "observations": [], "interpretations": [], "bindings": []}
        count = len(Chem.SDMolSupplier(str(files["predicted"]), removeHs=False))
        if count != historical_verifier["expected_pose_slots"]:
            raise ValueError("historical slot count differs")
        qualification_records, refs = [], {}
        for index in range(count):
            artifact = write_input_qualification(folder / f"qualification-{index}.json", bundle, workspace=folder / "source-workspace.json",
                stages=[{"name": "reference", "source_id": "reference", "origin": "source", "record_index": 0},
                        {"name": "predicted", "source_id": "predicted", "origin": "native_output", "record_index": index}],
                endpoint={"name": "fixed_frame_rmsd", "policy": POLICY, "reference": "reference", "prediction": "predicted"}, purpose="evaluation")
            qualification_records.append({"record_id": artifact.id, "record_type": "artifact", "visibility": "released", "record": artifact.as_dict()})
            refs[str(index)] = make_ref(cid + "-qualifications", artifact.id, artifact.as_dict())
        qmanifest = folder / "qualification-manifest.json"
        save(qmanifest, {"schema": "etalon-artifact-manifest/1", "records": qualification_records})
        workspace["sources"].append({"source_id": cid + "-qualifications", "kind": "artifact_manifest", "location": str(qmanifest),
                                     "identity": {"kind": "snapshot_sha256", "value": identity(qmanifest)["sha256"]}})
        save(folder / "workspace.json", workspace)
        kwargs = {"check_contract": contract, "qualification_refs": refs, "workspace": folder / "workspace.json",
                  "expected_pose_count": count, "expected_inputs": pins}
        start = time.perf_counter()
        legacy = verify_poses(files["predicted"], files["reference"], files["receptor"])
        legacy_seconds = time.perf_counter() - start
        start = time.perf_counter()
        endpoint = verify_pose_endpoints(files["predicted"], files["reference"], files["receptor"], **kwargs)
        endpoint_seconds = time.perf_counter() - start
        integrity = validate_endpoint_verifier(endpoint, **kwargs)
        if integrity["status"] != "complete":
            raise ValueError("new endpoint integrity failed: " + canonical(integrity))
        if cid != "1M2Z_DEX" and any(row["joint"]["value"] is not None for row in endpoint["rows"]):
            raise ValueError("unknown historical reference was promoted")
        save(folder / "legacy-current-environment.json", legacy)
        save(folder / "endpoint-verification.json", endpoint)
        save(folder / "endpoint-integrity.json", integrity)
        summaries[cid] = {"historical_top1": historical["top1"], "historical_verifier": historical["verifier"],
            "legacy_physical_observed": sum(bool(row["checks"]) and row["error"] is None for row in legacy["rows"]),
            "endpoint_availability": integrity["endpoint_availability"], "slot_count": count,
            "reference_qualification": sorted({row["qualification"]["eligibility"]["status"] for row in endpoint["rows"]}),
            "new_top1": {name: endpoint["rows"][0][name] for name in ("physical", "rmsd", "joint")},
            "legacy_seconds": legacy_seconds, "endpoint_seconds": endpoint_seconds}
    if any(identity(path) != pin for path, pin in originals.items()):
        raise ValueError("archived original changed during reassessment")
    receipt = {"schema": "etalon-pose-endpoint-reassessment/1", "request_digest": digest(request), "cases": summaries,
               "originals_unchanged": True, "originals": originals, "modules": modules,
               "runtime": {"executable": sys.executable, "python": sys.version, "rdkit": rdBase.rdkitVersion,
                           "posebusters": importlib.metadata.version("posebusters"),
                           "historical_runtime_difference": "historical Linux RDKit 2026.03.1; current local runtime recorded; no cross-environment equivalence claim"},
               "cost": {"wall_seconds": time.perf_counter() - started, "new_docking_calls": 0, "new_md_calls": 0,
                        "legacy_verifier_calls": len(CASES), "endpoint_verifier_calls": len(CASES)},
               "scope": "diagnostic information availability, not pose/affinity improvement or independent science",
               "outputs": {str(p.relative_to(output)): identity(p) for p in output.rglob("*") if p.is_file()}}
    save(output / "receipt.json", receipt)
    print(canonical({"state": "completed", "cases": summaries, "cost": receipt["cost"]}))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.archive, args.output)
