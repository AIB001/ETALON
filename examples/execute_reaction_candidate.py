"""Evaluate supplied route revisions with an existing AiZynthFinder install.

Run in the chosen AiZynthFinder environment; this example installs nothing and
does not run a search. Input hashes, literal templates and scoring endpoints are
frozen in a request file before execution. The output is ordinary JSON for the
host's existing observation/feedback interface.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

from etalon.research.reaction_candidate import (
    chemical_identity, chemical_state, compile_reaction_candidate, object_digest,
    reaction_edges, walk_nodes,
)


def pin(path):
    path = Path(path)
    data = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def evaluate_tree(tree, config):
    from aizynthfinder.chem import Molecule, TemplatedRetroReaction, TreeMolecule
    from aizynthfinder.reactiontree import ReactionTree

    started, cpu = time.perf_counter(), time.process_time()
    evaluated = deepcopy(tree)
    evaluated.pop("scores", None)
    evaluated.pop("metadata", None)
    stock_rows = []
    for path, node in walk_nodes(evaluated):
        if node.get("type") != "mol":
            continue
        mol = Molecule(smiles=node["smiles"], sanitize=True)
        node["in_stock"] = mol in config.stock
        if not path:
            node["in_stock"] = False
        if not node.get("children"):
            stock_rows.append({"path": list(path), **chemical_state(node["smiles"]),
                               "in_stock": node["in_stock"],
                               "availability": config.stock.availability_string(mol)})
    edges = []
    for path, product, reaction, precursors in reaction_edges(evaluated):
        edge_start, edge_cpu = time.perf_counter(), time.process_time()
        metadata = reaction.get("metadata", {})
        row = {"path": list(path), "product": chemical_state(product["smiles"]),
               "expected_precursors": [chemical_state(node["smiles"]) for node in precursors],
               "candidate_operation": metadata.get("candidate_operation"),
               "template": metadata.get("template"), "status": "FAILED",
               "experimental_success_observed": False, "experimental_yield": None}
        try:
            if not row["template"]:
                raise ValueError("native edge has no executable template")
            use_rdchiral = metadata.get("template_engine") != "rdkit_grouped_materials"
            row["template_engine"] = "rdchiral" if use_rdchiral else "rdkit_grouped_materials"
            retro = TemplatedRetroReaction(
                TreeMolecule(parent=None, smiles=product["smiles"], sanitize=True),
                smarts=row["template"], metadata=deepcopy(metadata), use_rdchiral=use_rdchiral)
            generated = [[chemical_identity(mol.smiles) for mol in outcome]
                         for outcome in retro.reactants]
            expected = Counter(chemical_identity(node["smiles"]) for node in precursors)
            matches = [index for index, outcome in enumerate(generated) if Counter(outcome) == expected]
            row.update(generated_precursor_outcomes=generated,
                       exact_expected_outcome_indices=matches,
                       template_application_reproduces_expected=bool(matches))
            if matches:
                retro.index = matches[0]
                passed, score = config.filter_policy["uspto"].feasibility(retro)
                row.update(filter_score=float(score), filter_passed=bool(passed),
                           filter_cutoff=config.filter_policy["uspto"].filter_cutoff,
                           actual_reaction_smiles=retro.reaction_smiles(), status="EVALUATED")
            else:
                row.update(filter_score=None, filter_passed=None, status="EXPECTED_OUTCOME_NOT_GENERATED")
        except Exception as exc:
            row.update(error_type=type(exc).__name__, error=str(exc))
        row["cost"] = {"wall_seconds": time.perf_counter() - edge_start,
                       "cpu_seconds": time.process_time() - edge_cpu}
        edges.append(row)
    native_tree = ReactionTree.from_dict(evaluated)
    score_names = ["state score", "number of reactions", "number of pre-cursors",
                   "number of pre-cursors in stock"]
    scores = {name: float(config.scorers[name](native_tree)) for name in score_names}
    return {"native_tree": native_tree.to_dict(), "native_tree_hash": native_tree.hash_key(),
            "stock_closed": native_tree.is_solved, "leaf_stock": stock_rows,
            "scores": scores, "edges": edges,
            "all_expected_outcomes_generated": all(edge.get("template_application_reproduces_expected") for edge in edges),
            "all_edges_filter_passed": all(edge.get("filter_passed") is True for edge in edges),
            "cost": {"wall_seconds": time.perf_counter() - started,
                     "cpu_seconds": time.process_time() - cpu},
            "interpretation": "Template reproduction and native heuristic evaluation; not independent chemical prediction or synthesis validation"}


def assemble_case_evaluation(*, case_id, target, stocks, compilation, baseline, candidate, cost):
    """Separate a completed native evaluation from its per-edge outcomes.

    ``verification`` remains the immutable compile-time snapshot, including its
    legacy false ``native_execution_completed`` flag. Completion here means
    both evaluations returned; failed templates or filter rejection stay in
    their respective edge results and do not become successful chemistry.
    """
    required = {"native_tree", "edges", "scores", "stock_closed",
                "all_expected_outcomes_generated", "all_edges_filter_passed"}
    if any(not required.issubset(result) for result in (baseline, candidate)):
        raise ValueError("completed native evaluation results are required")
    verification = deepcopy(compilation["verification"])
    verification.setdefault("stage", "compile_time")
    return {"id": case_id, "target": target, "stocks": stocks,
            "verification": verification,
            "execution": {"status": "completed", "native_execution_completed": True,
                "scope": "baseline_and_candidate_native_evaluation",
                "edge_outcome_locations": ["baseline.edges", "candidate.edges"],
                "lab_experiment_executed": False},
            "baseline": baseline, "candidate": candidate, "cost": cost}


def run(request_path, output_directory):
    started, cpu = time.perf_counter(), time.process_time()
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=False)
    request_path = Path(request_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    save(output / "started.json", {"request": pin(request_path), "pid": os.getpid(),
                                  "started_unix_seconds": time.time()})
    for identity in request["input_files"]:
        if pin(identity["path"])["sha256"] != identity["sha256"]:
            raise ValueError(f"input identity changed: {identity['path']}")
    for name in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"]:
        os.environ[name] = "2"
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    import yaml
    from aizynthfinder.context.config import Configuration
    config_data = yaml.safe_load(Path(request["native_config_path"]).read_text())
    # The existing filter and stock are consumed; expansion models are unnecessary.
    config_data.pop("expansion", None)
    config = Configuration.from_dict(config_data)
    config.filter_policy.select("uspto")
    loaded_cost = {"wall_seconds": time.perf_counter() - started,
                   "cpu_seconds": time.process_time() - cpu}
    result = {"schema": "etalon-native-reaction-evaluation/1", "request": pin(request_path),
              "runtime": {"executable": sys.executable, "python": sys.version,
                "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
                "distributions": {name: importlib.metadata.version(name)
                                  for name in ["aizynthfinder", "rdkit", "rdchiral", "onnxruntime"]},
                "modules": {name: pin(importlib.import_module(name).__file__)
                            for name in ["etalon.research.reaction_candidate", "aizynthfinder.chem.reaction",
                                         "aizynthfinder.reactiontree", "aizynthfinder.context.policy.filter_strategies",
                                         "aizynthfinder.context.scoring.scorers", "rdkit", "onnxruntime"]}},
              "cost": {"setup": loaded_cost}, "cases": [],
              "new_searches": 0, "new_lab_experiments": 0, "full_synthetic_yield": None,
              "filter_scope": "Existing USPTO model, not calibrated for these conditional salt/isolation operations",
              "conditions_scope": "Stored as source context; the fingerprint filter does not consume solvent, drying, catalyst or workup"}
    for case in request["cases"]:
        case_start, case_cpu = time.perf_counter(), time.process_time()
        config.stock.select(case["stocks"])
        native = json.loads(Path(case["native_path"]).read_text(encoding="utf-8"))
        tree = native["trees"][case["binding"]["tree_index"]]
        compile_start, compile_cpu = time.perf_counter(), time.process_time()
        candidate = compile_reaction_candidate(tree, case["binding"], case["operations"])
        compile_cost = {"wall_seconds": time.perf_counter() - compile_start,
                        "cpu_seconds": time.process_time() - compile_cpu}
        save(output / (case["id"] + ".candidate.json"), candidate)
        baseline = evaluate_tree(tree, config)
        revised = evaluate_tree(candidate["tree"], config)
        row = assemble_case_evaluation(
            case_id=case["id"], target=case["binding"]["target"], stocks=case["stocks"],
            compilation=candidate, baseline=baseline, candidate=revised,
            cost={"compile": compile_cost, "wall_seconds": time.perf_counter() - case_start,
                  "cpu_seconds": time.process_time() - case_cpu})
        save(output / (case["id"] + ".evaluation.json"), row)
        result["cases"].append(row)
    result["execution"] = {"status": "completed", "native_execution_completed": True,
                           "completed_case_count": len(result["cases"]),
                           "edge_outcome_location": "cases[].baseline.edges and cases[].candidate.edges",
                           "lab_experiment_executed": False}
    result["cost"]["total"] = {"wall_seconds": time.perf_counter() - started,
                                  "cpu_seconds": time.process_time() - cpu}
    save(output / "evaluation.json", result)
    print(json.dumps({"result": pin(output / "evaluation.json"), "cost": result["cost"],
                      "cases": [{"id": case["id"],
                        "generated": case["candidate"]["all_expected_outcomes_generated"],
                        "filter_pass": case["candidate"]["all_edges_filter_passed"],
                        "stock_closed": case["candidate"]["stock_closed"]} for case in result["cases"]]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("output_directory")
    args = parser.parse_args()
    run(args.request, args.output_directory)
