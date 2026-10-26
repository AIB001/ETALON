"""Compile a supplied local route revision for an existing reaction consumer.

This optional helper has no reaction model, stock, executor or research store.
It preserves exact chemical states and untouched precursor subtrees. The caller
supplies executable templates and must evaluate the resulting candidate with its
chosen native tool. Template reproduction is not evidence of synthesis success.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors


def chemical_identity(smiles: str) -> str:
    """Canonical, stereo-sensitive identity without reaction atom-map labels.

    No desalting, neutralization or tautomer standardization is performed.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"invalid molecular SMILES: {smiles}")
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    return Chem.MolToSmiles(mol, isomericSmiles=True)


def chemical_state(smiles: str) -> dict:
    canonical = chemical_identity(smiles)
    mol = Chem.MolFromSmiles(canonical)
    return {"smiles": canonical, "formula": rdMolDescriptors.CalcMolFormula(mol),
            "formal_charge": Chem.GetFormalCharge(mol),
            "component_count": len(Chem.GetMolFrags(mol)),
            "component_charges": sorted(Chem.GetFormalCharge(part)
                                         for part in Chem.GetMolFrags(mol, asMols=True))}


def bind_pose_route(molecule_smiles: str, *, pose_refs: list[dict],
                    route_path, route_identity: dict, observation_refs=()) -> dict:
    """Link poses to one complete retained route without copying it per pose.

    The producer supplies the actual pose graph/state, not a molecule nickname.
    Exact canonical identity retains stereochemistry, charge, salt and tautomer.
    A different graph remains a research candidate with an unmatched route; the
    host can seek another route instead of inheriting this route's scores.
    This reads existing bytes only and neither runs chemistry nor ranks a pose.
    Pose/observation refs keep their caller-owned provenance and read boundary.
    """
    path = Path(route_path).absolute()
    raw = path.read_bytes()
    actual = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    if actual != route_identity:
        raise ValueError("retained route original identity changed")
    payload = json.loads(raw.decode("utf-8-sig"))
    tree = payload["tree"]
    if tree.get("type") != "mol":
        raise ValueError("complete route must start with a molecule")
    molecule = chemical_state(molecule_smiles)
    target = chemical_state(tree["smiles"])
    edges = list(reaction_edges(tree))
    matched = molecule["smiles"] == target["smiles"]
    leaves = [{"smiles": chemical_identity(node["smiles"]),
               "in_stock": node.get("in_stock")}
              for _, node in walk_nodes(tree)
              if node.get("type") == "mol" and not node.get("children")]
    return {"schema": "etalon-pose-route-binding/1", "molecule": molecule,
        "pose_refs": deepcopy(pose_refs), "observation_refs": deepcopy(list(observation_refs)),
        "status": "matched" if matched else "route_target_mismatch",
        "route": {"artifact": {"path": str(path), **actual}, "tree_pointer": "/tree",
            "tree_sha256": object_digest(tree), "root_state": target,
            "reaction_count": len(edges), "leaves": leaves},
        "route_evaluation": deepcopy(payload.get("execution_reuse")) if matched else None,
        "experimental_scope": deepcopy(payload.get("experimental_scope")) if matched else None,
        "decision_boundary": "Same identity permits joint consideration, not transfer of pose benefit to synthesis yield or of route score to binding. Full route remains in the pinned original; unknown observations stay unknown.",
        "new_scientific_calls": 0}


def object_digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def walk_nodes(node: dict, path: tuple[int, ...] = ()):
    yield path, node
    for index, child in enumerate(node.get("children", [])):
        yield from walk_nodes(child, path + (index,))


def reaction_edges(tree: dict):
    """Yield product, reaction and precursor objects without modifying them."""
    for path, node in walk_nodes(tree):
        if node.get("type") != "mol":
            continue
        children = node.get("children", [])
        if children:
            if len(children) != 1 or children[0].get("type") != "reaction":
                raise ValueError("a route molecule must have exactly one reaction")
            reaction = children[0]
            reactants = reaction.get("children", [])
            if not reactants or any(item.get("type") != "mol" for item in reactants):
                raise ValueError("a route reaction must contain molecule precursors")
            yield path, node, reaction, reactants


def route_dependency_manifest(tree: dict) -> dict:
    """Separate graph/template, process and leaf observations of a full route.

    These are dependency descriptions, not an executable cache. In particular,
    graph-only reuse also needs the caller's original engine/model/environment
    identity. A process change cannot reuse a process-sensitive result merely
    because the graph matches. Subtree digests include all retained metadata.
    """
    if tree.get("type") != "mol":
        raise ValueError("complete route must start with a molecule")
    edges, processes, leaves, subtrees = [], [], [], []
    for path, node in walk_nodes(tree):
        if node.get("type") not in ("mol", "reaction"):
            raise ValueError("route nodes must alternate molecules and reactions")
        if node["type"] == "mol":
            state = chemical_identity(node["smiles"])
            subtrees.append({"path": list(path), "smiles": state,
                             "sha256": object_digest(node)})
            if not node.get("children"):
                leaves.append({"path": list(path), "smiles": state,
                               "in_stock": node.get("in_stock")})
    for path, product, reaction, reactants in reaction_edges(tree):
        metadata = reaction.get("metadata", {})
        spec = {"product": chemical_identity(product["smiles"]),
                "precursors": sorted(chemical_identity(n["smiles"]) for n in reactants),
                "template": metadata.get("template"),
                "template_engine": metadata.get("template_engine", "rdchiral")}
        edges.append({"path": list(path), **spec, "edge_sha256": object_digest(spec)})
        processes.append({"path": list(path), "edge_sha256": object_digest(spec),
                          "candidate_operation": deepcopy(metadata.get("candidate_operation")),
                          "condition_candidate": deepcopy(metadata.get("condition_candidate"))})
    return {"schema": "etalon-route-dependencies/1",
            "root_smiles": chemical_identity(tree["smiles"]),
            "graph_template": edges, "process": processes,
            "compiled_leaf_observations": leaves, "subtrees": subtrees,
            "tree_sha256": object_digest(tree)}


def pose_route_dependency_keys(molecule_smiles: str, *, pose_dependencies: dict,
                               route_manifest: dict, route_method: dict,
                               inventory_dependencies: dict | None = None) -> dict:
    """Key related computations independently; never execute an artificial reuse.

    pose_dependencies must contain the full pose readout request (coordinates,
    chemical state, receptor/preparation, protocol and method/runtime pins).
    route_method must contain the original native engine/model/runtime identity.
    Completeness of these caller-owned manifests remains the caller's duty.
    Inventory epochs and process/source evidence deliberately have separate keys.
    """
    molecule = chemical_identity(molecule_smiles)
    if molecule != route_manifest["root_smiles"]:
        raise ValueError("changed molecule needs its own compatible full route")
    if not pose_dependencies or not route_method:
        raise ValueError("complete pose and route method dependencies are required")
    graph = [{k: v for k, v in e.items() if k != "edge_sha256"}
             for e in route_manifest["graph_template"]]
    return {"pose": object_digest({"molecule": molecule, "dependencies": pose_dependencies}),
            "route_graph": object_digest({"molecule": molecule, "graph": graph,
                                          "method": route_method}),
            "route_process": object_digest({"molecule": molecule,
                                            "process": route_manifest["process"]}),
            "inventory": None if inventory_dependencies is None else object_digest({
                "leaves": [{"path": n["path"], "smiles": n["smiles"]}
                           for n in route_manifest["compiled_leaf_observations"]],
                "dependencies": inventory_dependencies})}


def choose_pose_route(*, poses: list[dict], routes: list[dict], objective: str,
                      pose_requirements: list[str], route_requirements: list[str]) -> dict:
    """Optional task-local shortlist and next actions, without a weighted score.

    Each option has id, molecule_smiles and facts. Facts have value (bool/None),
    scope and evidence_refs. Pose refs must identify coordinates; route options
    carry a bind_pose_route result. Requirements are the host's stated objective,
    not universal synthesis or pose admission rules. A failed requirement means
    'not selected for this objective'; an unknown remains unresolved. Neither
    implies scientific impossibility. Ties are retained, not arbitrarily ranked.

    Caller-supplied next_actions are ordinary research proposals, never executed
    here. Route-only actions are returned once even if multiple poses share them.
    """
    if not objective:
        raise ValueError("a scoped research objective is required")
    assessments = {"pose": [], "route": []}
    eligible = {"pose": [], "route": []}
    for kind, options, required in (("pose", poses, pose_requirements),
                                    ("route", routes, route_requirements)):
        if len(set(required)) != len(required):
            raise ValueError("duplicate requirement")
        ids = [row["id"] for row in options]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate {kind} id")
        for row in options:
            molecule = chemical_identity(row["molecule_smiles"])
            if kind == "pose" and not row.get("pose_ref"):
                raise ValueError("pose coordinates need an evidence reference")
            incompatible = False
            if kind == "route":
                binding = row["binding"]
                incompatible = (binding.get("status") != "matched" or
                    molecule != binding["route"]["root_state"]["smiles"] or
                    molecule != binding["molecule"]["smiles"])
            facts = row.get("facts", {})
            for name, fact in facts.items():
                value = fact.get("value")
                if value is not None and type(value) is not bool:
                    raise ValueError(f"{name} must be true, false or unknown")
                if not fact.get("scope") or (value is not None and not fact.get("evidence_refs")):
                    raise ValueError(f"{name} needs scope and evidence for known values")
            missing = [name for name in required if facts.get(name, {}).get("value") is None]
            failed = [name for name in required if facts.get(name, {}).get("value") is False]
            status = ("incompatible_route" if incompatible else "not_selected" if failed
                      else "unresolved" if missing else "supported_for_objective")
            assessments[kind].append({"id": row["id"], "status": status,
                                      "failed_requirements": failed,
                                      "unknown_requirements": missing})
            if status == "supported_for_objective":
                eligible[kind].append(row)
    pairs = [{"pose_id": pose["id"], "route_id": route["id"],
              "molecule_smiles": chemical_identity(pose["molecule_smiles"])}
             for pose in eligible["pose"] for route in eligible["route"]
             if chemical_identity(pose["molecule_smiles"]) ==
                chemical_identity(route["molecule_smiles"])]
    actions = []
    for kind, id_key in (("pose", "pose_id"), ("route", "route_id")):
        selected = {pair[id_key] for pair in pairs}
        for row in eligible[kind]:
            if row["id"] in selected:
                actions.extend({"depends_on": {id_key: row["id"]}, **deepcopy(action)}
                               for action in row.get("next_actions", []))
    if not pairs:
        actions.append({"kind": "resolve_pose_requirements" if not eligible["pose"]
                        else "resolve_compatible_route_requirements",
                        "assessment_refs": ["/assessments/pose", "/assessments/route"]})
    return {"schema": "etalon-pose-route-choice/1", "objective": objective,
            "requirements": {"pose": list(pose_requirements), "route": list(route_requirements)},
            "assessments": assessments, "selected_pairs": pairs, "next_actions": actions,
            "status": "research_shortlist" if pairs else "unresolved",
            "weighted_score": None, "experimental_winner_claimed": False,
            "new_scientific_calls": 0}


def _material(smiles: str) -> dict:
    return {"type": "mol", "hide": False, "smiles": chemical_identity(smiles),
            "is_chemical": True, "in_stock": False}


def compile_reaction_candidate(tree: dict, binding: dict, operations: list[dict]) -> dict:
    """Replace one exact edge with an ordered forward list of local operations.

    Each operation supplies product_smiles, reactant_smiles, retro_smarts and
    source_anchor. Existing precursor branches are reused in full when they
    occur at the replacement boundary. New material availability stays false
    until a native stock consumer re-evaluates it. Old route scores are removed.
    """
    if not operations:
        raise ValueError("an explicit nonempty operation chain is required")
    original_digest = object_digest(tree)
    edge = binding["replaced_edge"]
    expected_product = chemical_identity(edge["product_smiles"])
    expected_reactants = Counter(map(chemical_identity, edge["reactant_smiles"]))
    matches = [(path, product, reaction, reactants)
               for path, product, reaction, reactants in reaction_edges(tree)
               if chemical_identity(product["smiles"]) == expected_product
               and Counter(chemical_identity(item["smiles"]) for item in reactants)
               == expected_reactants
               and reaction.get("smiles") == edge["native_reaction"]]
    if len(matches) != 1:
        raise ValueError(f"expected one exact native edge, found {len(matches)}")
    path, old_product, old_reaction, boundary = matches[0]
    reusable = {}
    for node in boundary:
        key = chemical_identity(node["smiles"])
        if key in reusable:
            raise ValueError("duplicate boundary materials need an explicit split")
        reusable[key] = node
    intermediates = {}
    used_boundary = []
    normalized = []
    for index, operation in enumerate(operations):
        operation = deepcopy(operation)
        for key in ("retro_smarts", "source_anchor"):
            if not operation.get(key):
                raise ValueError(f"operation {index} needs {key}")
        product = chemical_identity(operation["product_smiles"])
        reactants = list(map(chemical_identity, operation["reactant_smiles"]))
        if not reactants or product in reactants or product in intermediates:
            raise ValueError("empty, self-referential or duplicate operation")
        if index and normalized[-1]["product_smiles"] not in reactants:
            raise ValueError("operation chain changes intermediate chemical state")
        children = []
        for smiles in reactants:
            if smiles in intermediates:
                children.append(deepcopy(intermediates[smiles]))
            elif smiles in reusable:
                children.append(deepcopy(reusable[smiles]))
                used_boundary.append(smiles)
            else:
                children.append(_material(smiles))
        operation.update(product_smiles=product, reactant_smiles=reactants)
        node = _material(product)
        node["children"] = [{"type": "reaction", "hide": False,
            "is_reaction": True, "smiles": product + ">>" + ".".join(reactants),
            "metadata": {"candidate_operation": operation,
                         "template": operation["retro_smarts"],
                         "template_engine": "rdkit_grouped_materials",
                         "policy_name": "supplied_literature_candidate",
                         "experimental_success_observed": False},
            "children": children}]
        intermediates[product] = node
        normalized.append(operation)
    if normalized[-1]["product_smiles"] != expected_product:
        raise ValueError("replacement does not restore the exact boundary product")
    if Counter(used_boundary) != expected_reactants:
        raise ValueError("replacement must preserve each original precursor subtree once")
    candidate = deepcopy(tree)
    candidate.pop("scores", None)
    candidate.pop("metadata", None)
    cursor = candidate
    for index in path:
        cursor = cursor["children"][index]
    cursor["children"] = intermediates[expected_product]["children"]
    cursor["in_stock"] = False
    # Only the chosen reaction and route-level score/metadata may change.
    masked_original = deepcopy(tree)
    masked_candidate = deepcopy(candidate)
    for route in (masked_original, masked_candidate):
        route.pop("scores", None)
        route.pop("metadata", None)
        node = route
        for index in path:
            node = node["children"][index]
        node["children"] = []
        node["in_stock"] = False
    if masked_original != masked_candidate or object_digest(tree) != original_digest:
        raise ValueError("compilation modified an unrelated route region")
    return {"schema": "etalon-local-reaction-candidate/1", "tree": candidate,
            "binding": deepcopy(binding), "operations": normalized,
            "verification": {"stage": "compile_time",
                "original_tree_sha256": original_digest,
                "candidate_tree_sha256": object_digest(candidate),
                "replaced_product_path": list(path),
                "untouched_route_sha256": object_digest(masked_original),
                "outside_fragment_unchanged": True,
                "reused_precursor_subtrees": [{"smiles": key, "sha256": object_digest(value)}
                                             for key, value in reusable.items()],
                "intermediate_states": [chemical_state(op["product_smiles"]) for op in normalized],
                "old_route_scores_inherited": False,
                # Compatibility field: this snapshot precedes native execution.
                "native_execution_completed": False,
                "lab_synthesis_validated": False,
                "full_synthetic_yield": None}}
