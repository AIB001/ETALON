"""Make a scoped joint choice from retained QOV pose and full-route evidence.

This reads the previous release's real originals, not synthetic scientific
scores. It performs no MD, route search, template execution or wet experiment.
The chosen input pose is a reference for further work, not an improved molecule.
Run with --release-entry PATH/entry.json --output NEW_DIRECTORY.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

from rdkit import Chem, rdBase
from etalon.research import reaction_candidate as rc


def pin(path):
    path = Path(path).absolute()
    raw = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def read(path, expected=None):
    actual = pin(path)
    if expected is not None and any(actual[k] != expected[k] for k in ("sha256", "bytes")):
        raise ValueError(f"retained evidence changed: {path}")
    return json.loads(Path(path).read_text(encoding="utf-8-sig")), actual


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def fact(value, scope, *refs):
    return {"value": value, "scope": scope, "evidence_refs": list(refs)}


def sdf_identity(path, expected):
    actual = pin(path)
    if any(actual[k] != expected[k] for k in ("sha256", "bytes")):
        raise ValueError(f"pose original changed: {path}")
    mol = Chem.SDMolSupplier(str(path), removeHs=False)[0]
    if mol is None:
        raise ValueError(f"invalid retained pose: {path}")
    return rc.chemical_identity(Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=True)), actual


def make_poses(release, bindings):
    science = release / "science"
    freeze, freeze_ref = read(science / "PAIR_FREEZE.json")
    protocol, protocol_ref = read(science / "PROTOCOL.json")
    _, source_ref = read(science / "SOURCE_FREEZE.json")
    rows = []
    for pair in freeze["pairs"]:
        run_id = pair["run_id"]
        for arm in ("candidate", "input"):
            result_path = science / "remote/primary" / run_id / arm / "result.json"
            result, result_ref = read(result_path)
            if result["status"] != "succeeded" or result["native_status"]["status"] != "succeeded":
                raise ValueError("failed native pose work must retain an unknown endpoint")
            original = pair["candidate"]["coordinates"] if arm == "candidate" else pair["exact_input_sdf"]
            path = (release.parent / "etalon-qov-anchor-20261026/science/remote/candidates" /
                    Path(original["path"]).name if arm == "candidate" else
                    science / "remote/pairs" / run_id / "exact-input.sdf")
            molecule, pose_ref = sdf_identity(path, original)
            if molecule != rc.chemical_identity(pair["chemical_identity"]["canonical_isomeric_smiles"]):
                raise ValueError("actual pose SDF differs from preparation identity")
            if molecule != rc.chemical_identity(result["chemical_identity"]["canonical_isomeric_smiles"]):
                raise ValueError("actual pose SDF differs from observed result identity")
            if arm == "candidate" and not any(
                    r["sha256"] == pose_ref["sha256"] for b in bindings for r in b["pose_refs"]):
                raise ValueError("candidate is absent from published full-route join")
            contract, contract_ref = read(result_path.parent / "mdjob/contract.json",
                                          result["native_handle"]["contract"])
            endpoint = result["readout"]["targets"]["ASN281_or_ARG255"]
            quality = result["final_quality"]
            measured_final = (endpoint["final"]["best_DA_A"] <= protocol["endpoints"]["primary_DA_A_max"]
                              and endpoint["final"]["best_DHA_deg"] >= protocol["endpoints"]["primary_DHA_deg_min"])
            if measured_final != endpoint["final"]["primary"]:
                raise ValueError("final contact measurements contradict the frozen primary endpoint")
            expected_retention = (endpoint["late_fraction"] >= 0.5 and
                                  endpoint["final"]["primary"] and quality["acceptable"])
            if expected_retention != result["retained_local_anchor"]:
                raise ValueError("saved retention contradicts frozen endpoint components")
            # This request describes the already executed matched basin probe;
            # it is not a new evaluation on a bare SDF or a cache execution.
            dependencies = {"pose": original, "pair_freeze": freeze_ref,
                            "preparation_and_native_request": contract_ref,
                            "source_freeze": source_ref, "protocol": protocol_ref,
                            "native_result": result_ref,
                            "receptor_and_box": pair["input_coordinates"],
                            "producer_inputs": pair["producer_inputs"]}
            rows.append({"id": f"{run_id}:{arm}", "molecule_smiles": molecule,
                "pose_ref": pose_ref, "dependencies": dependencies,
                "facts": {
                    "retained_local_anchor": fact(result["retained_local_anchor"],
                        protocol["endpoints"]["retained_local_anchor"] +
                        "; selected 100 ps restrained basin probe, not affinity", result_ref, protocol_ref),
                    "quality": fact(quality["acceptable"],
                        "Final exact-state geometry/strain/clash checks under original protocol", result_ref)},
                "measurements": {"late_occupancy": endpoint["late_fraction"],
                    "late_frames": result["readout"]["late_frames"],
                    "final_DA_A": endpoint["final"]["best_DA_A"],
                    "final_DHA_deg": endpoint["final"]["best_DHA_deg"],
                    "quality": quality},
                "next_actions": [{"kind": "retain_pose_as_local_reference",
                    "scope": "Use this previously computed input state as a comparator; no pose improvement or equilibrium claim"}]})
    return rows, [freeze_ref, protocol_ref, source_ref]


def make_routes(bindings):
    rows = []
    for old in bindings:
        route_ref = old["route"]["artifact"]
        route, _ = read(route_ref["path"], route_ref)
        binding = rc.bind_pose_route(old["molecule"]["smiles"], pose_refs=[],
            route_path=route_ref["path"], route_identity={k: route_ref[k] for k in ("sha256", "bytes")})
        manifest = rc.route_dependency_manifest(route["tree"])
        native_ref = route["provenance"]["native_evaluation"]
        native, _ = read(native_ref["path"], native_ref)
        case = native["cases"][route["provenance"]["native_case_index"]]
        if case["id"] != route["id"]:
            raise ValueError("native route case mismatch")
        graph = [{k: v for k, v in e.items() if k != "edge_sha256"}
                 for e in manifest["graph_template"]]
        if rc.object_digest(graph) != route["provenance"]["reaction_model_inputs_sha256"]:
            raise ValueError("assembled route no longer matches evaluated graph/template inputs")
        source_steps = []
        for path, product, reaction, reactants in rc.reaction_edges(route["tree"]):
            metadata = reaction.get("metadata", {})
            op = metadata.get("candidate_operation")
            condition = metadata.get("condition_candidate")
            supplied = op or condition
            if supplied and (rc.chemical_identity(supplied["product_smiles"]) !=
                    rc.chemical_identity(product["smiles"]) or
                    Counter(map(rc.chemical_identity, supplied["reactant_smiles"])) !=
                    Counter(rc.chemical_identity(n["smiles"]) for n in reactants)):
                raise ValueError("source-supported operation does not match the route edge")
            if op:
                if not op.get("source_doi") or not op.get("source_anchor"):
                    raise ValueError("fragment source requires exact recorded source anchor")
                support = "recorded_exact_fragment_precedent"
            elif condition:
                support = "recorded_analogue_transfer_unperformed"
            else:
                support = "native_graph_template_only"
            source_steps.append({"path": list(path), "product": rc.chemical_identity(product["smiles"]),
                "support": support, "source_anchor": (op or condition or {}).get("source_anchor"),
                "source_doi": (op or condition or {}).get("source_doi"),
                "source_url": (op or condition or {}).get("source_url")})
        # This selects source coverage for the NEXT research action. A recorded
        # fragment precedent does not establish this full route's actual yield.
        upstream_supported = all(step["support"] == "recorded_exact_fragment_precedent"
                                 for step in source_steps if step["path"])
        inventory = case["candidate"]["leaf_stock"]
        missing = [leaf["smiles"] for leaf in inventory if leaf["in_stock"] is not True]
        discrepancies = []
        by_path = {tuple(n["path"]): n for n in inventory}
        for leaf in manifest["compiled_leaf_observations"]:
            observed = by_path[tuple(leaf["path"])]
            if rc.chemical_identity(observed["smiles"]) != leaf["smiles"]:
                raise ValueError("inventory leaf chemical state mismatch")
            if observed["in_stock"] != leaf["in_stock"]:
                discrepancies.append({"smiles": leaf["smiles"], "path": leaf["path"],
                    "compile_time_in_stock": leaf["in_stock"],
                    "completed_native_snapshot_in_stock": observed["in_stock"]})
        coupling_ref = route["provenance"]["coupling_evidence"]
        read(coupling_ref["path"], coupling_ref)
        rows.append({"id": route["id"], "molecule_smiles": binding["molecule"]["smiles"],
            "binding": binding, "dependency_manifest": manifest,
            "route_method": {"runtime": native["runtime"], "request": native["request"],
                             "original_result": native_ref},
            "inventory_dependencies": {"native_result": native_ref, "stocks": case["stocks"],
                                       "leaf_stock": inventory},
            "facts": {
                "native_graph_evaluated": fact(case["candidate"]["all_expected_outcomes_generated"]
                    and case["candidate"]["all_edges_filter_passed"],
                    "Completed graph-only template/filter check, no process or yield model", native_ref),
                "upstream_source_supported": fact(upstream_supported,
                    "All upstream fragment steps have recorded exact precedent; final analogue coupling kept separate", route_ref),
                "exact_target_process_observed": fact(None,
                    "No exact QOV final coupling yield or whole-route experiment established", coupling_ref),
                "procurement_confirmed": fact(None,
                    "Historical database membership does not confirm current stock, price, lot or shipment", native_ref)},
            "source_steps": source_steps,
            "inventory_resolution": {"missing_in_fixed_native_stock": missing,
                "compile_vs_native_discrepancies": discrepancies,
                "action_completed": "Read completed native leaf observations instead of using compile-time flags",
                "current_supplier_availability": None},
            "next_actions": [
                {"kind": "resolve_material_sourcing", "materials": missing,
                 "scope": "Confirm chemical state/catalog coverage and actual procurement; absent from this fixed database is not unavailable"},
                {"kind": "evaluate_exact_target_coupling", "evidence_refs": [coupling_ref],
                 "scope": "Source-qualified analogue process is a test candidate; target conversion, isolated yield and identity need their own evidence",
                 "experimental_yield": None}],
            "full_synthetic_yield": route["experimental_scope"]["full_synthetic_yield"]})
    return rows


def run(release_entry, output, science_handoff=None):
    start, cpu = time.perf_counter(), time.process_time()
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    entry, entry_ref = read(release_entry)
    if not entry.get("checkpoint_ref"):
        raise ValueError("expected published release entry")
    release = Path(release_entry).absolute().parent
    bound, bound_ref = read(release / "handoff/POSE_ROUTE_BINDING.json")
    poses, source_refs = make_poses(release, bound["bindings"])
    routes = make_routes(bound["bindings"])
    objective = ("Retain a previously observed local QOV reference and a complete route with "
        "recorded upstream source support for targeted sourcing/condition work; "
        "not selection of a synthesized drug or equilibrium binding mode")
    request = {"poses": poses, "routes": routes, "objective": objective,
               "pose_requirements": ["retained_local_anchor", "quality"],
               "route_requirements": ["native_graph_evaluated", "upstream_source_supported"]}
    choice = rc.choose_pose_route(**request)
    controls = {}
    for label, removed in (("identity_only", ("pose_requirements", "route_requirements")),
                           ("without_pose_evidence", ("pose_requirements",)),
                           ("without_route_source_evidence", ("route_requirements",))):
        args = deepcopy(request)
        for name in removed:
            args[name] = [] if name == "pose_requirements" else ["native_graph_evaluated"]
        controls[label] = rc.choose_pose_route(**args)
    keys = [{"pose_id": pose["id"], "route_id": route["id"],
             "keys": rc.pose_route_dependency_keys(pose["molecule_smiles"],
                 pose_dependencies=pose["dependencies"], route_manifest=route["dependency_manifest"],
                 route_method=route["route_method"], inventory_dependencies=route["inventory_dependencies"])}
            for pose in poses for route in routes]
    graph_keys = {row["keys"]["route_graph"] for row in keys}
    pose_keys = {row["keys"]["pose"] for row in keys}
    edge_sets = [{e["edge_sha256"] for e in r["dependency_manifest"]["graph_template"]} for r in routes]
    subtree_sets = [{n["sha256"] for n in r["dependency_manifest"]["subtrees"]} for r in routes]
    reuse = {"pair_keys": keys, "distinct_pose_requests": len(pose_keys),
             "distinct_full_route_graph_requests": len(graph_keys),
             "shared_edge_graph_specs": sorted(set.intersection(*edge_sets)),
             "exact_shared_subtrees_including_metadata": sorted(set.intersection(*subtree_sets)),
             "new_native_calls": 0, "new_route_searches": 0,
             "interpretation": "Dependency identities and existing results reused; no artificial execution to claim time savings. Equal edge specs still need compatible native method/model inputs."}
    additional = None
    if science_handoff is not None:
        body, ref = read(science_handoff)
        additional = {"ref": ref, "status": body.get("status"),
            "use": "Additional science context; does not retrospectively change the frozen local-retention endpoint or assert that local contact retention is the whole scientific objective."}
    result = {"schema": "etalon-qov-pose-route-study/1", "entry_ref": entry_ref,
        "published_binding_ref": bound_ref, "source_refs": source_refs,
        "choice": choice, "evidence_omission_checks": controls,
        "additional_science": additional,
        "comparison_scope": "Retrospective deterministic evidence-omission checks on seen originals; no blinded trial, algorithm speed comparison or new scientific replicate",
        "nonclaims": ["novel compound", "new binding affinity", "route yield", "procurement success", "end-to-end method gain"],
        "cost": {"wall_seconds": time.perf_counter() - start,
                 "cpu_seconds": time.process_time() - cpu, "new_scientific_calls": 0,
                 "new_lab_experiments": 0, "new_sources_requested": 0,
                 "historical_scientific_cost": "Retained originals; not charged as new execution or reported as free discovery",
                 "model_and_development_cost": None},
        "runtime": {"python": sys.executable, "python_version": sys.version,
                    "rdkit": rdBase.rdkitVersion, "module": pin(rc.__file__), "example": pin(__file__)}}
    save(output / "INPUT.json", request)
    save(output / "CHOICE.json", result)
    save(output / "DEPENDENCY_REUSE.json", reuse)
    expected = [{"pose_id": "7KNX-QOV-seed-202610242:input", "route_id": "QOV-C_catalog_100",
                 "molecule_smiles": poses[0]["molecule_smiles"]}]
    if choice["selected_pairs"] != expected:
        raise ValueError("observed evidence changed; inspect the saved choice rather than overwriting it")
    counts = {k: len(v["selected_pairs"]) for k, v in controls.items()}
    if counts != {"identity_only": 12, "without_pose_evidence": 6, "without_route_source_evidence": 2}:
        raise ValueError("evidence-omission check changed")
    if len(pose_keys) != 6 or len(graph_keys) != 2:
        raise ValueError("pose and route dependencies were accidentally coupled")
    return {"selected_pairs": choice["selected_pairs"], "control_pair_counts": counts,
            "choice": pin(output / "CHOICE.json"), "dependencies": pin(output / "DEPENDENCY_REUSE.json"),
            "cost": result["cost"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-entry", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--science-handoff", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.release_entry, args.output, args.science_handoff), ensure_ascii=False))
