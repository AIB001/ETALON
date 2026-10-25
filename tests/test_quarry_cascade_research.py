"""Unit checks on synthetic source rows; not public-source E2E evidence."""
import importlib.util
from pathlib import Path

import pytest
from rdkit import Chem


spec = importlib.util.spec_from_file_location(
    "quarry_chain_example", Path(__file__).parents[1] / "examples/quarry_cascade_research.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def source(smiles, cid):
    return {"CID": cid, "SMILES": smiles, "InChIKey": Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))}


def selected(smiles, cid):
    return {"cid": cid, "id": "fixture-" + str(cid), "evidence_class": "G",
            "state_smiles": smiles, "full_inchikey": source(smiles, cid)["InChIKey"]}


def test_public_response_order_does_not_change_identity_join():
    rows = example.review_records([source("OCC", 2), source("CO", 1)],
                                 [selected("CO", 1), selected("CCO", 2)])
    assert [(r["cid"], r["parent_task_candidate_id"]) for r in rows] == [(2, "fixture-2"), (1, "fixture-1")]
    assert all(r["activity_value"] is None and r["assay_admission"] == "not_performed" for r in rows)


@pytest.mark.parametrize("records", [[], [source("CO", 1), source("CO", 1)], [source("CO", 3)]])
def test_missing_duplicate_or_unrequested_public_identity_is_rejected(records):
    with pytest.raises(ValueError):
        example.review_records(records, [selected("CO", 1)])


def test_full_key_match_does_not_bypass_exact_state_comparison():
    # Standard InChI can collapse these tautomers. A graph comparison must
    # reject the change even when the full public/selected keys agree.
    keto, enol = "O=c1cccc[nH]1", "Oc1ccccn1"
    assert source(keto, 1)["InChIKey"] == source(enol, 1)["InChIKey"]
    with pytest.raises(ValueError, match="chemical state"):
        example.review_records([source(enol, 1)], [selected(keto, 1)])


def test_public_key_must_describe_the_actual_public_graph():
    record = {**source("CO", 1), "InChIKey": source("CCO", 2)["InChIKey"]}
    with pytest.raises(ValueError, match="identity differs"):
        example.review_records([record], [selected("CO", 1)])


def test_frozen_file_mutation_is_detected_before_execution(tmp_path):
    path = tmp_path / "input.dat"
    path.write_bytes(b"old")
    identity = example.pin(path)
    path.write_bytes(b"new")
    with pytest.raises(ValueError, match="input changed"):
        example.verify_pin(identity)
