"""Read two archived top-1 cases locally; never run docking or the verifier.

The pinned diagnostic Artifacts anchor each original SDF's byte identity through
an explicit JSON pointer. The existing frozen ResearchJournal is read-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

from etalon.active.schema import canonical
from etalon.research.evidence_refs import make_ref
from etalon.research.input_qualification import POLICY, read_input_qualification, write_input_qualification
from etalon.research.scientific_input import SCHEMA, identity


def save(path, value):
    data = (canonical(value) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("preserve prior output: " + str(path))
    else:
        path.write_bytes(data)


def run(archive, diagnosis, output):
    started = time.perf_counter()
    archive, diagnosis, output = (Path(p).absolute() for p in (archive, diagnosis, output))
    output.mkdir(parents=True, exist_ok=True)
    anchors = output / "anchors"
    anchors.mkdir(exist_ok=True)
    publication = diagnosis / "harness/.etalon/publications/27bb53f739b1c07bd3e44550"
    snapshot = publication / "snapshot.sqlite"
    snapshot_pin = identity(snapshot)
    source_id = "publication-3baa2eb5a3a02a8b9873"
    with sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        records = [(key.removeprefix("research:"), json.loads(body)) for key, body in
                   db.execute("SELECT key,body FROM metadata WHERE key LIKE 'research:artifact-%'")]
    sources, mapping, cases, pins = [], [], {}, {}
    for cid in ("1OWE_675", "1UOU_CMU"):
        report_path = diagnosis / (cid + "-diagnostic.json")
        report_pin = identity(report_path)
        rid, body = next((rid, body) for rid, body in records if body.get("sha256") == report_pin["sha256"])
        if {k: body[k] for k in ("sha256", "bytes")} != report_pin:
            raise ValueError("original diagnostic identity mismatch")
        local = anchors / (report_pin["sha256"] + ".json")
        if local.exists() and local.read_bytes() != report_path.read_bytes():
            raise ValueError("anchor copy changed")
        if not local.exists():
            local.write_bytes(report_path.read_bytes())
        mapping.append({"source_id": source_id, "original": body["path"], "location": str(local), **report_pin})
        report = json.loads(report_path.read_text(encoding="utf-8"))
        cases[cid] = (report, make_ref(source_id, rid, body))
        pins[str(report_path)] = report_pin
    workspace = output / "workspace.json"
    save(workspace, {"schema": "etalon-evidence-workspace/1", "workspace_id": "archived-imine-input-qualification/1",
                    "sources": [{"source_id": source_id, "kind": "journal", "location": str(snapshot),
                                 "identity": {"kind": "snapshot_sha256", "value": snapshot_pin["sha256"]}}],
                    "location_map": mapping})
    summary = {}
    for cid, (original, ref) in cases.items():
        bundle = {"schema": SCHEMA, "task_id": cid + "-archived-input-qualification", "scope": "seen archive; top-1 records only",
                  "subject": {"id": cid}, "sources": [], "observations": [], "interpretations": [], "bindings": []}
        stages = []
        for i, old in enumerate(original["stages"]):
            name = old["name"]
            relative = {"source": f"evaluator/{cid}/reference.sdf", "reference": f"evaluator/{cid}/reference.sdf",
                        "graph_regenerated_input": f"inputs/{cid}/ligand.sdf", "empirical": f"outputs/{cid}/empirical.sdf",
                        "cnn": f"outputs/{cid}/cnn.sdf"}[name]
            path = archive / relative
            pin = {k: old[k] for k in ("sha256", "bytes")}
            if identity(path) != pin:
                raise ValueError("original archive bytes differ: " + cid + " " + name)
            pins[str(path)] = pin
            bundle["sources"].append({"id": name, "role": "evaluation_only" if name in {"source", "reference"} else "context",
                                      "scope": bundle["scope"], "path": str(path), **pin,
                                      "locator": old["path"] + " SDF record 0 (identical local bytes)",
                                      "evidence_ref": ref, "identity_pointer": ["stages", i]})
            stages.append({"name": name, "source_id": name, "record_index": 0,
                           "origin": "source" if name in {"source", "reference"} else "generated" if name == "graph_regenerated_input" else "native_output"})
        endpoint = {"name": "historical_strict_pose_identity", "policy": POLICY, "reference": "reference", "prediction": "cnn"}
        artifact = write_input_qualification(output / (cid + "-qualification.json"), bundle, stages=stages,
                                             endpoint=endpoint, workspace=workspace)
        checked = read_input_qualification(artifact, workspace=workspace)
        assert checked["endpoint_eligibility"]["status"] == "unresolved_reference_comparability"
        assert checked["endpoint_eligibility"]["endpoint_value"] is None
        save(output / (cid + "-artifact.json"), artifact.as_dict())
        summary[cid] = {"artifact": artifact.as_dict(), "endpoint_eligibility": checked["endpoint_eligibility"],
                        "historical_endpoint_retained": original["original_endpoint"],
                        "source_ref": ref, "source_diagnostic_identity": identity(diagnosis / (cid + "-diagnostic.json"))}
    assert identity(snapshot) == snapshot_pin
    assert all(identity(path) == pin for path, pin in pins.items())
    import etalon.research.input_qualification as implementation
    from rdkit import Chem, rdBase
    receipt = {"schema": "etalon-input-qualification-demo/1", "cases": summary,
               "original_files_verified_before_and_after": pins, "journal_snapshot": {"path": str(snapshot), **snapshot_pin},
               "executable": sys.executable, "python": sys.version, "implementation": {"path": implementation.__file__, **identity(implementation.__file__)},
               "rdkit": {"version": rdBase.rdkitVersion, "module_path": Chem.__file__},
               "scope": "local read-only top-1 reanalysis; not historical runtime replication; full SDF file hashes verified",
               "native_executions": 0, "verifier_endpoint_executions": 0, "new_journals": 0,
               "originals_unchanged": True}
    save(output / "demo-receipt.json", receipt)
    print(json.dumps({"cases": list(summary), "status": "unresolved_reference_comparability", "wall_seconds": time.perf_counter() - started}))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--diagnosis", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.archive, args.diagnosis, args.output)
