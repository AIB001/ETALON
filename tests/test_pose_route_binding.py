"""Small chemical identity fixtures, not evidence of a synthetic route's yield."""
import hashlib
import json
from copy import deepcopy

import pytest
from etalon.research.reaction_candidate import (
    bind_pose_route, choose_pose_route, pose_route_dependency_keys, route_dependency_manifest,
)


def retained(tmp_path, smiles="C[C@H](O)F"):
    route = {"tree": {"type": "mol", "smiles": smiles, "children": [
        {"type": "reaction", "children": [
            {"type": "mol", "smiles": "CC=O", "in_stock": True},
            {"type": "mol", "smiles": "F", "in_stock": None}]}]},
        "execution_reuse": {"all_edges_filter_passed": True, "existing_stock_closed": False},
        "experimental_scope": {"full_synthetic_yield": None}}
    p = tmp_path / "route.json"
    p.write_text(json.dumps(route), encoding="utf-8")
    b = p.read_bytes()
    return p, {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}


def test_shared_full_route_keeps_unknown_and_does_not_copy_tree(tmp_path):
    p, identity = retained(tmp_path)
    before = p.read_bytes()
    result = bind_pose_route("C[C@H](O)F", pose_refs=[{"pose": "a"}, {"pose": "b"}],
                             route_path=p, route_identity=identity)
    assert result["status"] == "matched"
    assert result["experimental_scope"]["full_synthetic_yield"] is None
    assert result["route"]["leaves"][1]["in_stock"] is None
    assert result["route"]["reaction_count"] == 1
    assert "tree" not in result["route"]
    assert p.read_bytes() == before and result["new_scientific_calls"] == 0


@pytest.mark.parametrize("different", ["C[C@@H](O)F", "CC(O)F", "C[C@H]([O-])F", "C[C@H](O)Cl"])
def test_changed_graph_state_or_unspecified_stereo_cannot_inherit_route(tmp_path, different):
    p, identity = retained(tmp_path)
    result = bind_pose_route(different, pose_refs=[], route_path=p, route_identity=identity)
    assert result["status"] == "route_target_mismatch"
    assert result["route_evaluation"] is None and result["experimental_scope"] is None


def test_changed_original_is_not_accepted_under_old_identity(tmp_path):
    p, identity = retained(tmp_path)
    p.write_text(p.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="identity changed"):
        bind_pose_route("C[C@H](O)F", pose_refs=[], route_path=p, route_identity=identity)


def choice_options(tmp_path):
    """Contract fixture; values are not measured chemical/pose evidence."""
    p, identity = retained(tmp_path)
    binding = bind_pose_route("C[C@H](O)F", pose_refs=[], route_path=p, route_identity=identity)
    def fact(value):
        return {"value": value, "scope": "test fixture only", "evidence_refs": ["fixture"]}
    poses = [{"id": "observed", "molecule_smiles": "C[C@H](O)F", "pose_ref": "fixture-A",
              "facts": {"retention": fact(True)}},
             {"id": "lost", "molecule_smiles": "C[C@H](O)F", "pose_ref": "fixture-B",
              "facts": {"retention": fact(False)}},
             {"id": "unknown", "molecule_smiles": "C[C@H](O)F", "pose_ref": "fixture-C",
              "facts": {"retention": fact(None)}}]
    routes = [{"id": "source_supported", "molecule_smiles": "C[C@H](O)F", "binding": binding,
               "facts": {"support": fact(True), "yield_observed": fact(None)},
               "next_actions": [{"kind": "resolve_target_conditions", "yield": None}]},
              {"id": "template_only", "molecule_smiles": "C[C@H](O)F", "binding": binding,
               "facts": {"support": fact(False), "yield_observed": fact(None)}}]
    return {"poses": poses, "routes": routes, "objective": "fixture research choice",
            "pose_requirements": ["retention"], "route_requirements": ["support"]}


def test_both_evidence_types_change_choice_and_unknown_yield_stays_unknown(tmp_path):
    args = choice_options(tmp_path)
    before = deepcopy(args)
    joint = choose_pose_route(**args)
    assert args == before
    assert [(p["pose_id"], p["route_id"]) for p in joint["selected_pairs"]] == [
        ("observed", "source_supported")]
    assert joint["next_actions"][0]["yield"] is None
    assert joint["assessments"]["pose"][2]["status"] == "unresolved"
    assert len(choose_pose_route(**{**args, "pose_requirements": []})["selected_pairs"]) == 3
    assert len(choose_pose_route(**{**args, "route_requirements": []})["selected_pairs"]) == 2
    assert joint["weighted_score"] is None and not joint["experimental_winner_claimed"]


def test_missing_requirement_does_not_become_false_or_pass(tmp_path):
    args = choice_options(tmp_path)
    args["poses"][0]["facts"].clear()
    result = choose_pose_route(**args)
    assert result["status"] == "unresolved"
    assert result["assessments"]["pose"][0]["unknown_requirements"] == ["retention"]
    assert result["next_actions"][0]["kind"] == "resolve_pose_requirements"


@pytest.mark.parametrize("value", [1, 0.99, "true"])
def test_scores_cannot_silently_become_factual_passes(tmp_path, value):
    args = choice_options(tmp_path)
    args["poses"][0]["facts"]["retention"]["value"] = value
    with pytest.raises(ValueError, match="true, false or unknown"):
        choose_pose_route(**args)


def test_graph_edit_keeps_pose_but_needs_compatible_route(tmp_path):
    args = choice_options(tmp_path)
    args["poses"][0]["molecule_smiles"] = "C[C@@H](O)F"
    result = choose_pose_route(**args)
    assert result["selected_pairs"] == []
    assert result["next_actions"][0]["kind"] == "resolve_compatible_route_requirements"
    args["routes"][0]["molecule_smiles"] = "C[C@@H](O)F"
    result = choose_pose_route(**args)
    assert result["assessments"]["route"][0]["status"] == "incompatible_route"


def test_ties_and_one_route_action_for_multiple_poses_are_preserved(tmp_path):
    args = choice_options(tmp_path)
    args["pose_requirements"] = []
    result = choose_pose_route(**args)
    assert len(result["selected_pairs"]) == 3
    assert len(result["next_actions"]) == 1


def test_dependency_changes_are_local_and_subtrees_are_retained(tmp_path):
    p, _ = retained(tmp_path)
    tree = json.loads(p.read_text(encoding="utf-8"))["tree"]
    tree["children"][0]["metadata"] = {"template": "fixture-template",
        "candidate_operation": {"source_anchor": "fixture-v1"}}
    manifest = route_dependency_manifest(tree)
    kwargs = {"pose_dependencies": {"coordinates": "fixture-pose-A", "receptor": "fixture-R",
                                   "protocol": "fixture-protocol", "runtime": "fixture-runtime"},
              "route_manifest": manifest, "route_method": {"engine": "fixture-engine", "model": "fixture-model"},
              "inventory_dependencies": {"epoch": "fixture-stock-v1"}}
    first = pose_route_dependency_keys("C[C@H](O)F", **kwargs)
    changed = deepcopy(kwargs)
    changed["pose_dependencies"]["coordinates"] = "fixture-pose-B"
    second = pose_route_dependency_keys("C[C@H](O)F", **changed)
    assert [k for k in first if first[k] != second[k]] == ["pose"]
    changed = deepcopy(kwargs)
    changed["inventory_dependencies"]["epoch"] = "fixture-stock-v2"
    second = pose_route_dependency_keys("C[C@H](O)F", **changed)
    assert [k for k in first if first[k] != second[k]] == ["inventory"]
    modified_tree = deepcopy(tree)
    modified_tree["children"][0]["metadata"]["candidate_operation"]["source_anchor"] = "fixture-v2"
    changed = {**kwargs, "route_manifest": route_dependency_manifest(modified_tree)}
    second = pose_route_dependency_keys("C[C@H](O)F", **changed)
    assert [k for k in first if first[k] != second[k]] == ["route_process"]
    assert manifest["subtrees"][1:] == changed["route_manifest"]["subtrees"][1:]
    modified_tree["children"][0]["metadata"]["template"] = "changed-template"
    changed["route_manifest"] = route_dependency_manifest(modified_tree)
    third = pose_route_dependency_keys("C[C@H](O)F", **changed)
    assert third["route_graph"] != second["route_graph"]
    assert third["pose"] == second["pose"]
    with pytest.raises(ValueError, match="own compatible full route"):
        pose_route_dependency_keys("C[C@@H](O)F", **kwargs)
