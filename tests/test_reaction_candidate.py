"""Route-compilation contract fixtures; these do not establish synthetic yield."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from etalon.research.reaction_candidate import (
    chemical_identity, chemical_state, compile_reaction_candidate, object_digest,
)


@pytest.fixture
def example():
    acid = "CS(=O)(=O)O"
    chloride = "CS(=O)(=O)Cl"
    salt = "CS(=O)(=O)[O-].[K+]"
    molecule = lambda smiles, **kwargs: {"type": "mol", "smiles": smiles,
                                        "in_stock": False, **kwargs}
    acid_branch = molecule(acid, children=[{"type": "reaction", "smiles": "retained",
        "metadata": {"template": "retained-template"}, "children": [molecule("C"), molecule("OS(=O)O")]}])
    tree = molecule(chloride, children=[{"type": "reaction", "smiles": "old-template",
        "metadata": {"policy_probability": 0.9},
        "children": [acid_branch, molecule("O=S(Cl)Cl")]}],
        scores={"state score": 1.0}, metadata={"is_solved": True})
    binding = {"replaced_edge": {"product_smiles": chloride,
        "reactant_smiles": [acid, "O=S(Cl)Cl"], "native_reaction": "old-template"}}
    operations = [
        {"product_smiles": salt, "reactant_smiles": [acid, "[K+].[OH-]"],
         "retro_smarts": "supplied-template-1", "source_anchor": "fixture-salt"},
        {"product_smiles": chloride, "reactant_smiles": [salt, "O=S(Cl)Cl"],
         "retro_smarts": "supplied-template-2", "source_anchor": "fixture-chloride"},
    ]
    return tree, binding, operations


def test_replace_retains_original_branch_and_removes_stale_scores(example):
    tree, binding, operations = example
    original = deepcopy(tree)
    output = compile_reaction_candidate(tree, binding, operations)
    candidate = output["tree"]
    assert tree == original
    assert candidate["children"][0]["children"][0]["children"][0]["children"][0] == original["children"][0]["children"][0]
    assert "scores" not in candidate and "metadata" not in candidate
    assert "policy_probability" not in candidate["children"][0]["metadata"]
    assert output["verification"]["outside_fragment_unchanged"]
    assert output["verification"]["original_tree_sha256"] == object_digest(original)
    assert output["verification"]["lab_synthesis_validated"] is False


def test_salt_state_is_not_silently_desalted_or_neutralized():
    acid = chemical_identity("CS(=O)(=O)O")
    anion = chemical_identity("CS(=O)(=O)[O-]")
    salt = chemical_state("[K+].CS(=O)(=O)[O-]")
    assert len({acid, anion, salt["smiles"]}) == 3
    assert salt["component_count"] == 2
    assert salt["component_charges"] == [-1, 1]
    assert salt["formal_charge"] == 0


@pytest.mark.parametrize("defect", ["missing_edge", "wrong_state", "dropped_boundary", "missing_template", "wrong_product"])
def test_unsafe_splice_is_rejected(example, defect):
    tree, binding, operations = example
    if defect == "missing_edge":
        binding["replaced_edge"]["native_reaction"] = "other"
    elif defect == "wrong_state":
        operations[1]["reactant_smiles"][0] = "CS(=O)(=O)[O-]"
    elif defect == "dropped_boundary":
        operations[0]["reactant_smiles"][0] = "CO"
    elif defect == "missing_template":
        operations[0].pop("retro_smarts")
    elif defect == "wrong_product":
        operations[-1]["product_smiles"] = "CS(=O)(=O)F"
    with pytest.raises(ValueError):
        compile_reaction_candidate(tree, binding, operations)


def test_identity_retains_stereochemistry_and_strips_only_maps():
    assert chemical_identity("[CH3:7][OH:9]") == chemical_identity("CO")
    assert chemical_identity("C[C@H](O)F") != chemical_identity("C[C@@H](O)F")


@pytest.mark.parametrize("edge_status,generated,filter_passed", [
    ("EVALUATED", True, True),
    ("EVALUATED", True, False),
    ("EXPECTED_OUTCOME_NOT_GENERATED", False, False),
])
def test_completed_evaluation_keeps_compile_snapshot_and_failed_outcomes(
        example, edge_status, generated, filter_passed):
    spec = importlib.util.spec_from_file_location(
        "reaction_candidate_example", Path(__file__).parents[1] / "examples/execute_reaction_candidate.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    compilation = compile_reaction_candidate(*example)
    assert compilation["verification"]["stage"] == "compile_time"
    # Exercise compatibility with previously written compilation artifacts too.
    compilation["verification"].pop("stage")
    original = deepcopy(compilation)
    evaluation = {"native_tree": {}, "edges": [{"status": edge_status, "filter_passed": filter_passed}],
                  "scores": {}, "stock_closed": False,
                  "all_expected_outcomes_generated": generated,
                  "all_edges_filter_passed": filter_passed}
    row = runner.assemble_case_evaluation(
        case_id="fixture", target="fixture", stocks=[], compilation=compilation,
        baseline=evaluation, candidate=evaluation, cost={})
    assert compilation == original
    assert row["verification"]["stage"] == "compile_time"
    assert row["verification"]["native_execution_completed"] is False
    assert row["execution"]["status"] == "completed"
    assert row["execution"]["native_execution_completed"] is True
    assert row["execution"]["lab_experiment_executed"] is False
    assert row["candidate"] == evaluation
    with pytest.raises(ValueError, match="completed native evaluation"):
        runner.assemble_case_evaluation(
            case_id="fixture", target="fixture", stocks=[], compilation=compilation,
            baseline=evaluation, candidate={"status": "started"}, cost={})
