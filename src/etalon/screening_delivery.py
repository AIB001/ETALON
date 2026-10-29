"""Materialize existing screening decisions and poses; never repeat science.

The component's artifact contracts remain authoritative. This is a streaming
delivery adapter, not a new gate, selector or research ledger.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import csv
import gzip
import json
import math
from pathlib import Path
import shutil
import sqlite3
import tempfile

from etalon.active.schema import digest
from etalon.runtime.files import file_identity, _save, _consumer_lock


def screening_contract(pipeline):
    """Compact readout of the compiled choices, without prescribing a workflow."""
    stages = pipeline.get("stages", [])
    budgets, redocks, gates = [], [], []
    for stage in stages:
        if not stage.get("enabled", True):
            continue
        plugin, config = stage["plugin"], stage.get("config", {})
        if plugin.startswith("select."):
            budgets.append({"stage": stage["id"], "method": plugin,
                **{k: config[k] for k in ("target_count", "max_per_scaffold", "seed") if k in config},
                "meaning": "budget allocation, not molecular quality rejection"})
        if plugin.startswith("docking.redock_consistency@"):
            redocks.append({"stage": stage["id"], "keep_poses": config.get("keep_poses", True)})
        if config.get("metric_id") == "dock_redock_rmsd":
            gates.append({"stage": stage["id"],
                **{k: config.get(k) for k in ("maximum", "maximum_exclusive", "on_unscorable")}})
    return {"budget_selection": budgets, "redock": redocks, "redock_gates": gates,
            "rmsd_meaning": "independent prediction consistency; crystal recovery is a separate comparison",
            "source_partitions": [{"stage": s["id"], "fields": s.get("config", {}).get("selected_properties", [])}
                                  for s in stages if s["plugin"].startswith("source.sdf@")]}


def delivery_options(value, pipeline):
    """Optional completion output bound to the same submitted plan as the science."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"rmsd_threshold", "require_redock"}:
        raise ValueError("delivery accepts rmsd_threshold and require_redock")
    threshold = value.get("rmsd_threshold", 2.0)
    required = value.get("require_redock", False)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("delivery RMSD threshold must be positive and finite")
    if type(required) is not bool:
        raise ValueError("require_redock must be boolean")
    if required:
        contract = screening_contract(pipeline)
        gates = contract["redock_gates"]
        if (not contract["redock"] or not all(s["keep_poses"] for s in contract["redock"]) or not gates
                or any(g["maximum"] is None or g["maximum"] > threshold
                       or g["maximum"] == threshold and not g["maximum_exclusive"]
                       or g["on_unscorable"] != "reject" for g in gates)):
            raise ValueError("requested redock delivery needs retained poses and a strict compatible RMSD gate; revise the new plan")
    return {"rmsd_threshold": float(threshold), "require_redock": required}


def rmsd_acceptance(value, status, threshold=2.0):
    """Strict requested consistency cutoff; unknown is not a measured failure."""
    if status != "OK" or not isinstance(value, (float, int)) or not math.isfinite(value):
        return "unknown"
    return "accept" if value < threshold else "reject"


def _sdf(stream, block, parent, **tags):
    if not block:
        return False
    # Keep original coordinates and atom order. Names and tags are metadata only.
    lines = block.rstrip("\r\n").splitlines()
    lines[0] = parent
    stream.write("\n".join(lines) + "\n")
    for key, value in {"parent_id": parent, **tags}.items():
        if value is not None:
            stream.write(f">  <{key}>\n{value}\n\n")
    stream.write("\n$$$$\n")
    return True


def export_screen_delivery(workspace, run_id, output, *, locations=None, rmsd_threshold=2.0):
    """Serialize materialization; a partial export can resume without science."""
    target = Path(output).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _consumer_lock(target.parent / ("." + target.name + "-delivery")):
        return _export_screen_delivery(workspace, run_id, target, locations=locations,
                                       rmsd_threshold=rmsd_threshold)


def _export_screen_delivery(workspace, run_id, output, *, locations=None, rmsd_threshold=2.0):
    """Export all committed gates/selection records, top-one dock/redock poses.

    Failed or partial runs keep their actual state. Individual criterion counts
    are not additive funnel attrition. Existing gates are never modified by the
    requested RMSD assessment. Receptor relocation is explicit.
    """
    from etalon.boundary.screen import Screen
    import pyarrow.parquet as pq

    if not math.isfinite(rmsd_threshold) or rmsd_threshold <= 0:
        raise ValueError("a positive RMSD threshold is required")
    screen = Screen.open_existing(workspace)
    from molcascade.artifacts.store import LocalArtifactStore
    state = screen.state(run_id)
    if state is None:
        raise FileNotFoundError("screen run is absent")
    store = LocalArtifactStore(screen.workspace, create=False)
    stages = []
    for stage in state.committed:
        directory = store.artifact_directory(stage.artifact_id, verify=True)
        stages.append((stage, store.get_manifest(stage.artifact_id), directory))
    root = Path(output).absolute()
    key = digest({"version": 4, "run": run_id, "revision": state.revision_id,
                  "artifacts": [(s.stage_id, s.artifact_id) for s, _, _ in stages],
                  "status": state.status, "threshold": rmsd_threshold, "locations": locations or {}})
    receipt = root / "delivery.json"
    if receipt.exists():
        prior = json.loads(receipt.read_text(encoding="utf-8"))
        if prior["request_key"] != key:
            raise ValueError("delivery changed; select a new output directory")
        for name, pin in prior["files"].items():
            if file_identity(root / name) != pin:
                raise ValueError("delivery output changed: " + name)
        return {**prior, "reused": True}
    marker = root / "request.json"
    if root.exists():
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("request_key") != key:
            raise ValueError("output directory belongs to another export; select a new directory")
    else:
        root.mkdir(parents=True)
        _save(marker, {"request_key": key, "run_id": run_id})
    for name in ("gates", "poses", "receptors"):
        (root / name).mkdir(exist_ok=True)

    def paths(manifest, directory, contract):
        return [directory / str(p) for o in manifest.outputs if o.contract_id == contract for p in o.file_paths]

    def batches(files, columns=None):
        for p in files:
            yield from pq.ParquetFile(p).iter_batches(batch_size=500, columns=columns)

    def count(files):
        return sum(pq.ParquetFile(p).metadata.num_rows for p in files)

    # Follow only registration's explicit source references. A later tier need
    # not copy the original metadata into every parent artifact to retain it.
    raw_files, origin_gaps, seen_origins = set(), [], set()
    verified = {(str(store.root), stage.artifact_id) for stage, _, _ in stages}

    def source_inputs(owner, manifest):
        identity = (str(owner.root), manifest.artifact_id)
        if identity in seen_origins:
            return
        seen_origins.add(identity)
        directory = owner.artifact_directory(manifest.artifact_id, verify=identity not in verified)
        raw_files.update(paths(manifest, directory, "raw_molecule/v1"))
        for ref in manifest.inputs:
            if ref.contract_id == "raw_molecule/v1":
                source_inputs(owner, owner.get_manifest(ref.artifact_id))
        if manifest.producer.plugin_id == "source.registered_parents":
            from etalon.generate.handoff import _locate
            config = manifest.metadata["plugin_config"]
            upstream_path = _locate(config["path"], locations or {})
            if not upstream_path.is_dir():
                raise FileNotFoundError(f"registration origin store unavailable: {upstream_path}")
            upstream = LocalArtifactStore(upstream_path, create=False)
            source_inputs(upstream, upstream.get_manifest(config["artifact_id"]))

    for _, manifest, _ in stages:
        try:
            source_inputs(store, manifest)
        except FileNotFoundError as error:
            origin_gaps.append({"artifact_id": manifest.artifact_id, "error": str(error)})

    # Temporary join index, removed after materialization. Do not load a million
    # molecule bodies into host context or create a second persistent registry.
    with tempfile.TemporaryDirectory(prefix="etalon-delivery-") as temp:
        with closing(sqlite3.connect(str(Path(temp) / "join.sqlite"))) as db:
            db.execute("CREATE TABLE molecules (id TEXT PRIMARY KEY, smiles TEXT)")
            db.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, candidate TEXT, metadata TEXT)")
            db.execute("CREATE TABLE origins (source TEXT, parent TEXT, PRIMARY KEY(source,parent))")
            candidates = [(count(ps), ps) for _, m, d in stages if (ps := paths(m, d, "parent/v1"))]
            parent_files = max(candidates, key=lambda item: item[0])[1] if candidates else []
            for b in batches(parent_files, ["parent_id", "parent_smiles"]):
                db.executemany("INSERT OR IGNORE INTO molecules VALUES (?,?)",
                               zip(b.column(0).to_pylist(), b.column(1).to_pylist()))
            for _, m, d in stages:
                for b in batches(paths(m, d, "parent_source_map/v1"), ["source_record_id", "parent_id"]):
                    db.executemany("INSERT OR IGNORE INTO origins VALUES (?,?)", zip(b.column(0).to_pylist(), b.column(1).to_pylist()))
            for b in batches(sorted(raw_files),
                             ["source_record_id", "raw_smiles", "source_candidate_id", "source_metadata_json"]):
                for row in b.to_pylist():
                    if row["raw_smiles"]:
                        db.execute("INSERT OR IGNORE INTO molecules VALUES (?,?)", (row["source_record_id"], row["raw_smiles"]))
                    db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?)",
                        (row["source_record_id"], row["source_candidate_id"], row["source_metadata_json"]))
            db.execute("INSERT OR IGNORE INTO molecules SELECT o.source,m.smiles FROM origins o JOIN molecules m ON m.id=o.parent")
            db.commit()
            with gzip.open(root / "parent-origins.csv.gz", "wt", encoding="utf-8", newline="") as out:
                writer = csv.writer(out)
                writer.writerow(["parent_id", "smiles", "source_record_id", "source_candidate_id", "source_metadata_json"])
                writer.writerows(db.execute("SELECT o.parent,m.smiles,o.source,s.candidate,s.metadata FROM origins o "
                    "LEFT JOIN molecules m ON m.id=o.parent LEFT JOIN sources s ON s.id=o.source ORDER BY o.parent,o.source"))
            summary, receptors = [], {}
            for index, (stage, manifest, directory) in enumerate(stages):
                prefix = f"{index:03d}-{stage.stage_id}"
                counts = Counter()
                files = paths(manifest, directory, "decision/v1")
                inherited = stage.plugin.startswith("source.registered")
                inherited_refs = [str(p) for p in files] if inherited else []
                if inherited:
                    counts["inherited_decision_rows"] = count(files)
                    files = []  # Reference upstream decisions; do not duplicate them at every tier.
                selection = paths(manifest, directory, "selection_decision/v1")
                for role, selected_files in (("decisions", files), ("selection", selection)):
                    if not selected_files:
                        continue
                    name = f"gates/{prefix}-{role}.csv.gz"
                    fields = ["entity_id", "smiles", "smiles_status", "outcome", "reason", "rule", "detail"]
                    with gzip.open(root / name, "wt", encoding="utf-8", newline="") as out:
                        writer = csv.DictWriter(out, fieldnames=fields)
                        writer.writeheader()
                        for b in batches(selected_files):
                            rows = b.to_pylist()
                            ids = [r.get("entity_id", r.get("parent_id")) for r in rows]
                            known = dict(db.execute("SELECT id, smiles FROM molecules WHERE id IN (" +
                                ",".join("?" for _ in ids) + ")", ids))
                            for r, entity in zip(rows, ids):
                                outcome = ("SELECTED" if r["selected"] else "NOT_SELECTED") if role == "selection" else r["outcome"]
                                counts[role + ":" + outcome] += 1
                                smiles = known.get(entity)
                                counts["missing_smiles"] += not bool(smiles)
                                writer.writerow(dict(entity_id=entity, smiles=smiles,
                                    smiles_status="available" if smiles else "not_in_this_run_population",
                                    outcome=outcome, reason=r.get("reason_code"),
                                    rule=r.get("rule_id", r.get("quota_policy_id")),
                                    detail=r.get("detail", json.dumps(r, ensure_ascii=False) if role == "selection" else None)))
                config = manifest.metadata.get("plugin_config", {})
                if config.get("receptor_path"):
                    from etalon.generate.handoff import _locate
                    receptor = _locate(config["receptor_path"], locations or {})
                    identity = file_identity(receptor)
                    if config.get("receptor_sha256") and identity["sha256"] != config["receptor_sha256"]:
                        raise ValueError("prepared receptor differs from the executed configuration")
                    relative = f"receptors/{identity['sha256']}.pdb"
                    if relative not in receptors:
                        shutil.copyfile(receptor, root / relative)
                        receptors[relative] = {"source": config["receptor_path"], **identity}
                with (root / f"poses/{prefix}-dock.sdf").open("w", encoding="utf-8", newline="\n") as dock:
                    for b in batches(paths(manifest, directory, "docking_score/v1")):
                        for r in b.to_pylist():
                            if r["pose_rank"] == 0:
                                counts["dock_top1"] += _sdf(dock, r.get("pose_molblock"), r["parent_id"],
                                    receptor_id=r["receptor_id"], method_id=r["method_id"], score=r["score"], role="dock")
                with (root / f"poses/{prefix}-redock.sdf").open("w", encoding="utf-8", newline="\n") as redock, \
                     (root / f"poses/{prefix}-rmsd.csv").open("w", encoding="utf-8", newline="") as report:
                    writer = csv.DictWriter(report, fieldnames=["parent_id", "value", "status", "assessment", "threshold", "comparison"])
                    writer.writeheader()
                    for b in batches(paths(manifest, directory, "derived_metric/v1")):
                        for r in b.to_pylist():
                            if r["metric_id"] != "dock_redock_rmsd":
                                continue
                            source = json.loads(r.get("source_json") or "{}")
                            assessment = rmsd_acceptance(r["value"], r["status"], rmsd_threshold)
                            counts["rmsd:" + assessment] += 1
                            counts["redock_top1"] += _sdf(redock, source.get("redock_pose_molblock"), r["parent_id"],
                                method_id=r["method_id"], role="redock", rmsd=r["value"], requested_acceptance=assessment)
                            writer.writerow(dict(parent_id=r["parent_id"], value=r["value"], status=r["status"],
                                assessment=assessment, threshold=rmsd_threshold, comparison="prediction_consistency"))
                for p in (root / "poses").glob(prefix + "-*"):
                    if (p.suffix == ".sdf" and p.stat().st_size == 0) or (p.suffix == ".csv" and not any(k.startswith('rmsd:') for k in counts)):
                        p.unlink()
                bindings = manifest.metadata.get("input_bindings", {})
                binding = bindings.get("parents", bindings.get("primary", {}))
                producer = next(((m, d) for s, m, d in stages if s.stage_id == binding.get("stage_id")), None)
                input_files = [d / str(p) for m, d in [producer] for o in m.outputs
                               if o.port == binding.get("port") and o.contract_id == "parent/v1" for p in o.file_paths] if producer else []
                parent_input = count(input_files) if input_files else None
                parent_files = paths(manifest, directory, "parent/v1")
                parent_output = count(parent_files) if parent_files else None
                summary.append(dict(stage_id=stage.stage_id, state=stage.status, artifact_id=stage.artifact_id,
                    plugin=stage.plugin, counts=dict(counts), parent_output=parent_output,
                    parent_input=parent_input, net_parent_removal=parent_input-parent_output if parent_input is not None and parent_output is not None else None,
                    inherited_decision_refs=inherited_refs,
                    input_bindings=manifest.metadata.get("input_bindings", {}),
                    recorded_gate={k: config[k] for k in ("minimum", "maximum", "maximum_exclusive", "on_unscorable", "target_count", "max_per_scaffold", "seed") if k in config},
                    count_scope="inherited_registration_records" if stage.plugin.startswith("source.registered") else "stage_assessment_rows; sibling criteria are not additive attrition"))
    result = dict(schema="etalon-screen-delivery/1", request_key=key, run_id=run_id, run_status=state.status,
        revision_id=state.revision_id, stages=summary, receptors=receptors,
        source_coverage={"raw_datasets": len(raw_files), "unresolved_origins": origin_gaps},
        rmsd=dict(threshold=rmsd_threshold, operator="<", meaning="dock/redock prediction consistency; not crystal accuracy", original_gates_changed=False),
        pose_scope="all available top-one poses at each committed stage; no new conformers generated",
        files={p.relative_to(root).as_posix(): file_identity(p) for p in root.rglob("*") if p.is_file()},
        scientific_calls=0, reused=False)
    _save(receipt, result)
    return result
