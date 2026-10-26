"""Synthetic boundary counterexamples; physical outcomes below are test doubles."""
import copy
import json

import pytest

Chem = pytest.importorskip("rdkit.Chem")
pytest.importorskip("posebusters")
from rdkit.Chem import AllChem
from posebusters import PoseBusters
import pandas as pd

from etalon.research.evidence_refs import make_ref
from etalon.research.input_qualification import write_input_qualification
from etalon.research.pose_verifier import installed_pose_check_contract, verify_pose_endpoints
from etalon.research.result_integrity import validate_endpoint_verifier
from etalon.research.scientific_input import identity
from test_input_qualification import make_case


def setup_case(tmp_path, monkeypatch, *, unknown=False):
    smiles = "N=C(N)c1ccccc1" if unknown else "C[C@H](O)F"
    molecule = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(molecule, randomSeed=41) == 0
    bundle, args = make_case(tmp_path, [molecule, molecule])
    qualifier = write_input_qualification(tmp_path / "qualification.json", bundle, **args)
    manifest = tmp_path / "manifest.json"
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body["records"].append({"record_id": qualifier.id, "record_type": "artifact", "visibility": "released",
                            "record": qualifier.as_dict()})
    manifest.write_text(json.dumps(body), encoding="utf-8")
    workspace = tmp_path / "workspace.json"
    ws = json.loads(workspace.read_text(encoding="utf-8"))
    ws["sources"][0]["identity"]["value"] = identity(manifest)["sha256"]
    workspace.write_text(json.dumps(ws), encoding="utf-8")
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text("END\n", encoding="ascii")
    contract = installed_pose_check_contract()
    # A fixed synthetic physical observation isolates the endpoint-boundary logic.
    monkeypatch.setattr(PoseBusters, "bust", lambda self, **kw:
        pd.DataFrame([{name: True for name in contract["required_checks"]}]))
    files = [bundle["sources"][i]["path"] for i in (1, 0)] + [str(receptor)]
    kwargs = {"check_contract": contract, "qualification_refs": {"0": make_ref("fixture", qualifier.id, qualifier.as_dict())},
              "workspace": str(workspace), "expected_pose_count": 1,
              "expected_inputs": {key: identity(path) for key, path in zip(("predicted", "reference", "receptor"), files)}}
    return files, kwargs


def audit(report, kwargs):
    return validate_endpoint_verifier(report, **kwargs)


def test_unknown_reference_retains_independent_physical_observation(tmp_path, monkeypatch):
    files, kwargs = setup_case(tmp_path, monkeypatch, unknown=True)
    report = verify_pose_endpoints(*files, **kwargs)
    row = report["rows"][0]
    assert row["qualification"]["eligibility"]["status"] == "unresolved_reference_comparability"
    assert row["physical"]["status"] == "observed" and row["physical"]["value"] is True
    assert row["rmsd"]["value"] is row["joint"]["value"] is None
    assert audit(report, kwargs)["status"] == "complete"
    assert report["historical_results_modified"] is False


def test_resolved_control_retains_joint_requirement(tmp_path, monkeypatch):
    files, kwargs = setup_case(tmp_path, monkeypatch)
    report = verify_pose_endpoints(*files, **kwargs)
    assert report["rows"][0]["rmsd"]["value"] == pytest.approx(0)
    assert report["rows"][0]["joint"]["value"] is True
    assert audit(report, kwargs)["status"] == "complete"


def test_missing_required_check_does_not_pass_even_with_good_rmsd(tmp_path, monkeypatch):
    files, kwargs = setup_case(tmp_path, monkeypatch)
    checks = kwargs["check_contract"]["required_checks"]
    monkeypatch.setattr(PoseBusters, "bust", lambda self, **kw: pd.DataFrame([{k: True for k in checks[:-1]}]))
    report = verify_pose_endpoints(*files, **kwargs)
    assert report["rows"][0]["physical"]["value"] is None
    assert report["rows"][0]["rmsd"]["value"] == pytest.approx(0)
    assert report["rows"][0]["joint"]["value"] is None
    assert audit(report, kwargs)["status"] == "complete"


@pytest.mark.parametrize("change", ["joint", "checks", "qualification", "slot", "rmsd", "input", "threshold"])
def test_report_edits_cannot_forge_complete_evidence(tmp_path, monkeypatch, change):
    files, kwargs = setup_case(tmp_path, monkeypatch, unknown=change in {"joint", "qualification", "rmsd"})
    report = verify_pose_endpoints(*files, **kwargs)
    row = report["rows"][0]
    if change == "joint": row["joint"] = {"status": "observed", "value": True, "reason": "forged"}
    elif change == "checks": row["checks"].pop(next(iter(row["checks"])))
    elif change == "qualification": row["qualification"]["eligibility"]["identity_comparable"] = True
    elif change == "slot": row["pose_index"] = 1
    elif change == "rmsd": row["rmsd"] = {"status": "observed", "value": 0, "reason": "forged"}
    elif change == "input": report["inputs"] = {}
    elif change == "threshold": report["threshold_A"] = 3
    assert audit(report, kwargs)["status"] == "unknown"


def test_wrong_qualification_ref_keeps_error_and_does_not_stop_physical(tmp_path, monkeypatch):
    files, kwargs = setup_case(tmp_path, monkeypatch)
    kwargs["qualification_refs"]["0"]["record_digest"] = "f" * 64
    report = verify_pose_endpoints(*files, **kwargs)
    row = report["rows"][0]
    assert row["qualification"] is None
    assert row["physical"]["value"] is True and row["joint"]["value"] is None
    assert any(d["phase"] == "qualification" for d in row["diagnostics"])


def test_missing_expected_slots_not_dropped(tmp_path, monkeypatch):
    files, kwargs = setup_case(tmp_path, monkeypatch)
    kwargs["expected_pose_count"] = 2
    report = verify_pose_endpoints(*files, **kwargs)
    assert len(report["rows"]) == 2
    assert report["rows"][1]["slot_present"] is False
    assert report["rows"][1]["joint"]["value"] is None
    assert audit(report, kwargs)["status"] == "unknown"


def test_changed_input_is_not_absorbed_as_endpoint_unknown(tmp_path, monkeypatch):
    files, kwargs = setup_case(tmp_path, monkeypatch)
    from pathlib import Path
    Path(files[0]).write_text("changed original", encoding="utf-8")
    with pytest.raises(ValueError, match="identities"):
        verify_pose_endpoints(*files, **kwargs)


def test_reusing_qualification_for_another_slot_is_rejected(tmp_path, monkeypatch):
    files, kwargs = setup_case(tmp_path, monkeypatch)
    from pathlib import Path
    path = Path(files[0])
    original = path.read_text(encoding="utf-8")
    path.write_text(original + "$$$$\n" + original + "$$$$\n", encoding="utf-8")
    kwargs["expected_inputs"]["predicted"] = identity(path)
    kwargs["expected_pose_count"] = 2
    kwargs["qualification_refs"]["1"] = copy.deepcopy(kwargs["qualification_refs"]["0"])
    report = verify_pose_endpoints(*files, **kwargs)
    assert all(row["qualification"] is None and row["joint"]["value"] is None for row in report["rows"])
