"""Optional, explicit Quarry -> reviewed library -> native Screen example.

This is an engineering acceptance on seen SND1 structural controls. It does not
admit assay labels, restart an old task, or select a research branch for the host.
Each command publishes new files; inspect/resume never reacquires public data.
"""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
import time
from pathlib import Path

from etalon.data.artifacts import file_hash, read_snapshot, write_json


def pin(path):
    path = Path(path).absolute()
    return {"path": str(path), "sha256": file_hash(path), "bytes": path.stat().st_size}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def publish(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)
    return value


def verify_pin(value):
    if pin(value["path"]) != value:
        raise ValueError("input changed: " + value["path"])


def runtime():
    from etalon.boundary.infra import load
    components = {n: load(n).provenance() for n in ("molquarry", "molcascade")}
    modules = {}
    for name in ("etalon", "rdkit", "posebusters", "pyarrow", "scipy"):
        try:
            module = importlib.import_module(name)
            modules[name] = {**pin(module.__file__), "version": getattr(module, "__version__", None)}
        except ImportError as error:
            modules[name] = {"unavailable": str(error)}
    return {"python": sys.executable, "python_version": sys.version,
            "components": components, "modules": modules}


def acquire(root, panel_path, cids):
    from etalon.boundary.quarry import DataBudget
    from etalon.data.service import plan_data, run_data
    panel = load_json(panel_path)
    selected = [next(row for row in panel["parents"] if row["cid"] == cid) for cid in cids]
    if len(set(cids)) != len(cids) or not 2 <= len(cids) <= 8:
        raise ValueError("select 2-8 distinct explicit source CIDs")
    receptor = next(row for row in panel["receptors"] if row["id"] == "7KNW")
    for key in ("receptor", "source"):
        actual = pin(receptor[key])
        if {k: actual[k] for k in ("sha256", "bytes")} != receptor[key + "_identity"]:
            raise ValueError("parent receptor/source identity changed")
    publish(root / "selection.json", {"schema": "quarry-cascade-selection/1",
        "question": "Can existing components preserve public identity through native docking and handoff?",
        "task_id": "snd1-component-chain-20261025", "parent_panel": pin(panel_path),
        "selection_rule": "explicit seen structural controls; no score or activity selection",
        "candidates": selected, "receptor": receptor,
        "scope": "engineering acceptance, not independent chemistry or ranking/affinity validation",
        "old_task": "read-only originals; no recovery or resubmission", "r4b": "paused"})
    publish(root / "runtime-acquire.json", runtime())
    budget = DataBudget(max_requests=8, max_bytes=2_000_000, max_seconds=90)
    requests = {
        "pubchem-properties": {"kind": "query", "source": "pubchem", "operation": "properties",
            "parameters": {"identifier": ",".join(map(str, cids)), "namespace": "cid"}, "max_pages": 1},
        "rcsb-entry": {"kind": "query", "source": "rcsb", "operation": "entry",
            "parameters": {"pdb_id": "7KNW"}, "max_pages": 1},
        "rcsb-polymer": {"kind": "query", "source": "rcsb", "operation": "polymer_entity",
            "parameters": {"pdb_id": "7KNW", "entity_id": "1"}, "max_pages": 1},
    }
    receipts = {}
    for name, request in requests.items():
        plan = plan_data(request, budget=budget)
        publish(root / (name + "-plan.json"), plan)
        value = run_data(request, root, run_id=name, budget=budget, expected_plan_id=plan["plan_id"])
        receipts[name] = {"path": value["snapshot"], "snapshot_id": value["snapshot_id"],
                          "result": value["result"], "usage": value["usage"]}
    return publish(root / "acquisition.json", {"schema": "quarry-cascade-acquisition/1", "sources": receipts})


def review_records(records, selected):
    """Fail closed on missing, duplicate, extra, or changed source identities."""
    from rdkit import Chem
    wanted = {row["cid"]: row for row in selected}
    seen = set()
    result = []
    for record in records:
        cid = record["CID"]
        if cid not in wanted or cid in seen:
            raise ValueError("unexpected/duplicate public CID")
        seen.add(cid)
        smiles = record.get("SMILES")
        molecule = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) else None
        if molecule is None:
            raise ValueError("missing/invalid public structure")
        key = Chem.MolToInchiKey(molecule)
        if key != record["InChIKey"] or key != wanted[cid]["full_inchikey"]:
            raise ValueError("public identity differs from source or parent task")
        # Full InChIKey alone need not distinguish tautomers. Compare exact
        # canonical isomeric graphs as a separate chemical-state check.
        canonical = Chem.MolToSmiles(molecule, isomericSmiles=True)
        expected = Chem.MolFromSmiles(wanted[cid]["state_smiles"])
        if expected is None or canonical != Chem.MolToSmiles(expected, isomericSmiles=True):
            raise ValueError("public chemical state differs from parent task")
        result.append({"cid": cid, "source_inchikey": key, "canonical_smiles": canonical,
            "parent_task_candidate_id": wanted[cid]["id"], "decision": "screen_only",
            "role": "seen_crystallographic_control" if wanted[cid]["evidence_class"] == "G" else "seen_source_analogue",
            "assay_admission": "not_performed", "activity_value": None})
    if seen != set(wanted):
        raise ValueError("public response omitted a selected CID")
    return result


def review(root, reviewer):
    selection = load_json(root / "selection.json")
    source = read_snapshot(root / "data/pubchem-properties")
    if source["result"]["status"] != "complete":
        raise ValueError("incomplete public source cannot be reviewed here")
    records = load_json(root / "data/pubchem-properties/payload/records.json")
    rows = review_records(records, selection["candidates"])
    rcsb = read_snapshot(root / "data/rcsb-polymer")
    polymers = load_json(root / "data/rcsb-polymer/payload/records.json")
    if rcsb["result"]["status"] != "complete" or len(polymers) != 1:
        raise ValueError("missing structural target context")
    context = polymers[0]
    identifiers = context.get("rcsb_polymer_entity_container_identifiers", {})
    if (identifiers.get("entry_id") != "7KNW" or identifiers.get("entity_id") != "1"
            or "Q7KZF4" not in identifiers.get("uniprot_ids", [])
            or not any(x.get("ncbi_taxonomy_id") == 9606
                       for x in context.get("rcsb_entity_source_organism", []))):
        raise ValueError("target context does not identify human SND1 Q7KZF4")
    return publish(root / "review.json", {"schema": "quarry-cascade-review/1", "reviewer": reviewer,
        "source_snapshot_id": source["snapshot_id"], "selection": pin(root / "selection.json"),
        "candidates": rows, "target_context": polymers[0],
        "state_decision": "preserve exact PubChem and parent-panel neutral graph; no pH/pKa claim",
        "receptor_decision": "reuse exact prepared chain-A 7KNW bytes and known box from parent panel",
        "scientific_role": "seen engineering acceptance; crystal association is not quantitative affinity",
        "release_labels": False, "new_assay_labels": False})


def prepare(root, gnina):
    from etalon.boundary.infra import load
    load("molcascade")  # Pin before importing any component-native config class.
    from etalon.boundary.screen import Screen
    from etalon.data.library import prepare_library
    from molcascade.cascade.catalog import CRITERIA_BY_ID
    from molcascade.cascade.defaults import build_criterion, default_cascade
    from molcascade.cascade.models import CascadeConfig, TierConfig
    review = load_json(root / "review.json")
    verify_pin(review["selection"])
    original = read_snapshot(root / "data/pubchem-properties")
    if original["snapshot_id"] != review["source_snapshot_id"]:
        raise ValueError("review no longer matches source")
    # Reverify every selected identity before transforming anything.
    selection = load_json(root / "selection.json")
    if review_records(load_json(root / "data/pubchem-properties/payload/records.json"),
                      selection["candidates"]) != review["candidates"]:
        raise ValueError("review candidate decisions changed")
    library = prepare_library(root / "data/pubchem-properties", root, run_id="reviewed-library",
                              id_field="CID", smiles_field="SMILES")
    receptor = selection["receptor"]
    target = root / "inputs/receptor.pdb"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(receptor["receptor"], target)
    if file_hash(target) != receptor["receptor_identity"]["sha256"]:
        raise ValueError("reused receptor changed")
    base = default_cascade(target_count=len(review["candidates"]), include_redock=False)
    conformers = tuple(t for t in base.tiers if any(c.criterion == "ligand_conformers" for c in t.criteria))
    if len(conformers) != 1:
        raise ValueError("native conformer tier unavailable")
    spec = CRITERIA_BY_ID["docking_cnn_rescore"]
    option = next(o for o in spec.executable_options if o.id == "gnina")
    docking = build_criterion(spec, option, criterion_id="gnina_empirical", settings={
        "executable": str(Path(gnina).absolute()), "cnn_scoring": "none", "rank_by": "affinity",
        "exhaustiveness": 1, "num_modes": 2, "allow_cpu": True, "cpu_threads": 2,
        "seed": 20261025, "max_molecules": 8,
        "pose_quality": {"enabled": True, "relax": True, "discard_failing": True},
    }, gate_settings={"expected_score_kind": "VINA_KCAL_MOL", "expected_direction": "LOWER_STRONGER",
                      "minimum": None, "maximum": 100.0})
    box = receptor["box"]
    raw = base.model_dump(mode="json")
    # Native recall and exports retain source identifiers only when explicitly
    # mapped. Stable parent ids alone cannot recover the original source row.
    raw["library"]["id_column"] = "id"
    raw.update(name="snd1_component_chain", tiers=[t.model_dump(mode="json") for t in conformers] + [
        TierConfig(id="native_docking", title="CPU empirical docking; mandatory native pose QC",
                   criteria=(docking,)).model_dump(mode="json")],
        target={"name": "SND1_7KNW_seen_pocket", "receptor_path": str(target),
                "receptor_sha256": file_hash(target), "preparation": {"enabled": False},
                "box": dict(zip(("center_x", "center_y", "center_z", "size_x", "size_y", "size_z"),
                                box["center"] + box["size"]))},
        metadata={"task_id": "snd1-component-chain-20261025", "review_sha256": file_hash(root / "review.json"),
            "gate_scope": "maximum100 is a permissive engineering gate, not a calibrated affinity threshold",
            "cache_scope": "old P2 low-level protocol differs; no exact native Screen cache exists",
            "old_results": "retained read-only; not recomputed or substituted as native Screen evidence"})
    config = CascadeConfig.model_validate(raw)
    publish(root / "cascade-base.json", config.model_dump(mode="json"))
    screen = Screen(root / "screen", allow_copyleft=True)
    screen.with_handoff(root / "cascade-base.json", root / "cascade-handoff.json",
                        prefer="docked_pose", protonation_state_id="reviewed-pubchem-neutral-state/1")
    plan = screen.plan(root / "cascade-handoff.json", library=Path(library["snapshot"]) / "library.csv")
    publish(root / "runtime-screen.json", runtime())
    return publish(root / "screen-plan.json", {**plan.as_dict(), "review": pin(root / "review.json"),
        "library_snapshot_id": library["snapshot_id"], "gnina": pin(gnina), "receptor": pin(target),
        "workers": 1, "devices": ["cpu"], "cpu_threads": 2, "new_gpu_cost": 0,
        "cost_scope": "new protocol engineering acceptance; no scientific benefit claimed"})


def execute(root, resume=False):
    import resource  # POSIX native execution; source review also works on Windows.
    from etalon.boundary.screen import Screen
    frozen = load_json(root / "screen-plan.json")
    for name in ("review", "gnina", "receptor"):
        verify_pin(frozen[name])
    screen = Screen(root / "screen", allow_copyleft=True)
    plan = screen.plan(root / "cascade-handoff.json", library=root / "data/reviewed-library/library.csv")
    if plan.revision_id != frozen["revision_id"]:
        raise ValueError("Screen revision differs from the frozen plan")
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start, cpu = time.perf_counter(), time.process_time()
    result = screen.run(plan, run_id="snd1-native-screen", resume=resume, workers=1, devices=("cpu",))
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    receipt = {"schema": "quarry-cascade-execution/1", "mode": "resume_verified" if resume else "new_execution",
        "result": result.as_dict(), "wall_seconds": time.perf_counter() - start,
        "host_cpu_seconds": time.process_time() - cpu,
        "child_cpu_seconds": after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
        "gpu_seconds": 0, "scope": "execution only; acquisition/preparation overhead separate",
        "runtime": runtime()}
    return publish(root / ("resume-receipt.json" if resume else "execution-receipt.json"), receipt)


def inspect(root):
    from etalon.boundary.screen import Screen
    from etalon.research.components import inspect_component
    screen = Screen.open_existing(root / "screen")
    result = screen.state("snd1-native-screen")
    if result is None or not result.terminal:
        raise ValueError("Screen output absent or nonterminal")
    verified = {}
    for stage in result.committed:
        verified[stage.stage_id] = {"artifact_id": stage.artifact_id,
                                  "row_count": len(screen.read(stage.artifact_id))}
    handoff = screen.handoff(result)
    publish(root / "handoff.json", handoff)
    report = {"schema": "quarry-cascade-inspection/1", "result": result.as_dict(),
        "verified_stage_rows": verified, "metrics": screen.metrics(result),
        "recall": screen.recall(result.run_id, panel_size=len(load_json(root / "review.json")["candidates"])),
        "handoff_rows": len(handoff), "scientific_benefit": "not_evaluated",
        "assay_admission": "not_performed", "r4b": "paused"}
    if result.status == "SUCCEEDED":
        report["shortlist"] = Screen(root / "screen").export_shortlist(result.run_id, root / "shortlist.smi")
    publish(root / "inspection.json", report)
    materials = {"schema": "quarry-cascade-memory-materials/1", "activity": "verified_read_existing",
        "scientific_admission": "not_performed", "new_read_cost": None, "receipts": {},
        "claims": ["real public identity records passed a reviewed library into a native CPU docking Screen",
                   "retention is accounting, not biological active recall; handoff is not MD execution"],
        "boundaries": ["seen structural controls", "one known SND1 pocket", "permissive numerical gate",
                       "no activity label imported", "no ranking/affinity/method-transfer benefit evaluated"]}
    for name, capability, location, run_id in (
        ("quarry", "molquarry.data", root / "data/pubchem-properties", None),
        ("library", "etalon.library", root / "data/reviewed-library", None),
        ("screen", "molcascade.screen", root / "screen", result.run_id)):
        try:
            value = inspect_component(capability, location, run_id=run_id)
            path = root / "memory" / (name + "-receipt.json")
            publish(path, value)
            materials["receipts"][name] = pin(path)
        except Exception as error:
            materials["receipts"][name] = {"error": type(error).__name__, "detail": str(error)}
    materials["originals"] = {name: pin(root / name) for name in
        ("selection.json", "review.json", "screen-plan.json", "execution-receipt.json", "inspection.json", "handoff.json")}
    return publish(root / "memory-materials.json", materials)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("acquire", "review", "prepare", "run", "resume", "inspect"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--cids", type=int, nargs="+", default=[87054177, 87054166])
    parser.add_argument("--reviewer", default="ETALON host component-chain review/1")
    parser.add_argument("--gnina", type=Path)
    args = parser.parse_args()
    root = args.root.absolute()
    root.mkdir(parents=True, exist_ok=True)
    if args.command == "acquire": value = acquire(root, args.panel, args.cids)
    elif args.command == "review": value = review(root, args.reviewer)
    elif args.command == "prepare": value = prepare(root, args.gnina)
    elif args.command in {"run", "resume"}: value = execute(root, resume=args.command == "resume")
    else: value = inspect(root)
    print(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    main()
