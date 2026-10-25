"""Contract controls use generated fixtures, never a scientific endpoint."""
import copy
import json
from pathlib import Path

import pytest

Chem = pytest.importorskip("rdkit.Chem")
from rdkit.Chem import AllChem

from etalon.active.schema import digest
from etalon.research.evidence_refs import make_ref
from etalon.research.input_qualification import (
    POLICY, build_input_qualification, read_input_qualification, write_input_qualification,
)
from etalon.research.schema import Artifact
from etalon.research.scientific_input import SCHEMA, compile_input, identity


def make_case(tmp_path, molecules, roles=None):
    sources, records = [], []
    for i, mol in enumerate(molecules):
        path = tmp_path / f"molecule-{i}.sdf"
        path.write_text(mol if isinstance(mol, str) else Chem.MolToMolBlock(mol), encoding="utf-8")
        artifact = Artifact(str(path), **identity(path), source_kind="fixture",
                            role=(roles or ["diagnostic"] * len(molecules))[i], bindings={})
        records.append({"record_id": artifact.id, "record_type": "artifact", "visibility": "released", "record": artifact.as_dict()})
        sources.append({"id": str(i), "role": "conditioning", "scope": "fixture", "path": str(path),
                        **identity(path), "locator": "SDF record 0", "evidence_ref": make_ref("fixture", artifact.id, artifact.as_dict())})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": records}), encoding="utf-8")
    workspace = tmp_path / "workspace.json"
    workspace.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "fixture",
        "sources": [{"source_id": "fixture", "kind": "artifact_manifest", "location": str(manifest),
                     "identity": {"kind": "snapshot_sha256", "value": identity(manifest)["sha256"]}}], "path_map": []}), encoding="utf-8")
    bundle = {"schema": SCHEMA, "task_id": "fixture", "scope": "fixture", "subject": {"id": "fixture"},
              "sources": sources, "observations": [], "interpretations": [], "bindings": []}
    stages = [{"name": str(i), "source_id": str(i), "origin": "source" if i == 0 else "native_output"}
              for i in range(len(sources))]
    return bundle, {"stages": stages, "endpoint": {"name": "unmeasured-fixture", "policy": POLICY,
                   "reference": "0", "prediction": str(len(sources) - 1)}, "workspace": str(workspace)}


def mol(smiles):
    return Chem.MolFromSmiles(smiles)


def qualify(tmp_path, smiles):
    bundle, args = make_case(tmp_path, [mol(s) for s in smiles])
    return build_input_qualification(bundle, **args)


def test_real_tetrahedral_inversion_is_distinct_from_atom_reordering(tmp_path):
    a = mol("F[C@H](Cl)Br")
    same = Chem.RenumberAtoms(a, list(reversed(range(a.GetNumAtoms()))))
    bundle, args = make_case(tmp_path, [a, same, mol("F[C@@H](Cl)Br")])
    report = build_input_qualification(bundle, **args)
    assert all(r["event"] == "no_information_change" for r in report["transitions"][0]["feature_transitions"])
    assert any(r["event"] == "resolved_configuration_change" for r in report["transitions"][1]["feature_transitions"])
    assert report["endpoint_eligibility"]["status"] == "resolved_stereochemistry_mismatch"


def test_actual_e_z_inversion(tmp_path):
    report = qualify(tmp_path, ["F/C=C/F", "F/C=C\\F"])
    assert report["transitions"][0]["graph_state_compatible"]
    assert report["endpoint_eligibility"]["status"] == "resolved_stereochemistry_mismatch"


def test_unknown_to_perceived_is_information_transition_without_coordinate_inversion(tmp_path):
    m = Chem.AddHs(mol("N=C(N)c1ccccc1"))
    assert AllChem.EmbedMolecule(m, randomSeed=41) == 0
    block = Chem.MolToMolBlock(m)
    lines = block.splitlines()
    line = 4 + m.GetNumAtoms()
    assert int(lines[line][9:12]) == 3
    lines[line] = lines[line][:9] + "  0" + lines[line][12:]
    bundle, args = make_case(tmp_path, [block, "\n".join(lines)])
    args["stages"][0]["origin"] = "generated"
    report = build_input_qualification(bundle, **args)
    transition = next(r for r in report["transitions"][0]["feature_transitions"] if r["before"]["state"] == "explicit_unknown")
    assert transition["after"]["state"] == "resolved"
    assert transition["after"]["basis"] == "generated_coordinate_perception"
    assert transition["coordinate_configuration_change"] is False
    assert report["stages"][0]["atoms"] == report["stages"][1]["atoms"]
    assert report["endpoint_eligibility"]["status"] == "unresolved_reference_comparability"
    assert any(h["reason"] == "defines_double_bond_stereo" for h in report["stages"][1]["default_remove_hs_retained"])


def test_nonstereogenic_is_separate_from_unspecified(tmp_path):
    report = qualify(tmp_path, ["CC(=O)N", "CC(=O)N"])
    f = next(f for f in report["stages"][0]["features"] if f["kind"] == "double_bond")
    assert f["applicability"] == "nonstereogenic" and f["information"]["state"] is None
    assert report["endpoint_eligibility"]["identity_comparable"]
    assert report["endpoint_eligibility"]["endpoint_value"] is None


@pytest.mark.parametrize("left,right", [("[13CH3]CN", "CCN"), ("CC[NH3+]", "CCN"), ("[2H]C(F)Cl", "[H]C(F)Cl")])
def test_isotope_charge_and_isotopic_h_are_chemical_state(tmp_path, left, right):
    report = qualify(tmp_path, [left, right])
    assert not report["transitions"][0]["graph_state_compatible"]
    assert report["endpoint_eligibility"]["status"] == "graph_chemical_state_mismatch"


def test_atom_maps_do_not_manufacture_identity_change_and_symmetry_is_reported(tmp_path):
    a = mol("c1ccccc1")
    b = Chem.Mol(a)
    for i, atom in enumerate(b.GetAtoms()): atom.SetAtomMapNum(i + 1)
    bundle, args = make_case(tmp_path, [a, b])
    report = build_input_qualification(bundle, **args)
    assert report["transitions"][0]["graph_state_compatible"]
    assert report["transitions"][0]["mapping_count"] > 1


def test_source_identity_and_ref_digest_mutation_fail(tmp_path):
    bundle, args = make_case(tmp_path, [mol("CC")])
    bundle["sources"][0]["evidence_ref"]["record_digest"] = "1" * 64
    with pytest.raises(ValueError, match="digest"): build_input_qualification(bundle, **args)
    bundle, args = make_case(tmp_path, [mol("CC")])
    Path(bundle["sources"][0]["path"]).write_text(Chem.MolToMolBlock(mol("CCC")))
    bundle["sources"][0].update(identity(bundle["sources"][0]["path"]))
    with pytest.raises(ValueError, match="hash|identity"): build_input_qualification(bundle, **args)


def test_ordinary_artifact_and_reader_reject_forged_eligibility(tmp_path):
    bundle, args = make_case(tmp_path, [mol("FC=CF")])
    artifact = write_input_qualification(tmp_path / "qualification.json", bundle, **args)
    report = read_input_qualification(artifact, workspace=args["workspace"])
    assert artifact.source_kind == "fixture"
    assert artifact.bindings["evidence_refs"] == report["evidence_refs"]
    assert artifact == write_input_qualification(tmp_path / "qualification.json", bundle, **args)
    report["endpoint_eligibility"]["identity_comparable"] = True
    Path(artifact.path).write_text(json.dumps(report), encoding="utf-8")
    forged = {**artifact.as_dict(), **identity(artifact.path)}
    with pytest.raises(ValueError, match="claims"): read_input_qualification(forged, workspace=args["workspace"])


def test_evaluation_only_ref_cannot_be_laundered(tmp_path):
    bundle, args = make_case(tmp_path, [mol("CC")], roles=["independent_validation"])
    with pytest.raises(ValueError, match="non-conditioning"): build_input_qualification(bundle, **args)
    bundle["sources"][0]["role"] = "evaluation_only"
    artifact = write_input_qualification(tmp_path / "qualification.json", bundle, **args)
    assert artifact.role == "independent_validation"
    with pytest.raises(ValueError, match="non-conditioning"):
        read_input_qualification(artifact, workspace=args["workspace"], purpose="conditioning")
    with pytest.raises(ValueError, match="role"):
        read_input_qualification({**artifact.as_dict(), "role": "diagnostic"}, workspace=args["workspace"])


def test_transitive_evaluation_lineage_is_enforced(tmp_path):
    bundle, args = make_case(tmp_path, [mol("CC"), mol("CC")], roles=["independent_validation", "diagnostic"])
    p = tmp_path / "manifest.json"
    doc = json.loads(p.read_text())
    child = doc["records"][1]
    child["record"]["bindings"]["evidence_refs"] = [bundle["sources"][0]["evidence_ref"]]
    child["record_id"] = "artifact-" + digest(child["record"])
    bundle["sources"][1]["evidence_ref"] = make_ref("fixture", child["record_id"], child["record"])
    p.write_text(json.dumps(doc))
    ws = Path(args["workspace"]); d = json.loads(ws.read_text())
    d["sources"][0]["identity"]["value"] = identity(p)["sha256"]; ws.write_text(json.dumps(d))
    bundle["sources"][0]["role"] = "evaluation_only"
    with pytest.raises(ValueError, match="non-conditioning"): build_input_qualification(bundle, **args)


def test_old_scientific_input_compile_does_not_require_qualification(tmp_path):
    bundle, args = make_case(tmp_path, [mol("CC")])
    del bundle["sources"][0]["evidence_ref"]
    bundle["bindings"] = [{"id": "old", "target": "constraint", "use": "constraint", "basis_ids": ["0"],
                           "value": 3, "rationale": "existing interface fixture"}]
    before = copy.deepcopy(bundle)
    assert compile_input(bundle, allowed_targets={"constraint": "constraint"})["parameters"] == {"constraint": 3}
    assert bundle == before


@pytest.mark.parametrize("role", ["evaluation_only", "context"])
def test_json_pointer_cannot_strip_container_role(tmp_path, role):
    bundle, args = make_case(tmp_path, [mol("CC")])
    source = bundle["sources"][0]
    container = tmp_path / "container.json"
    container.write_text(json.dumps({"role": role, "source": {k: source[k] for k in ("sha256", "bytes")}}))
    artifact = Artifact(str(container), **identity(container), source_kind="fixture", role="diagnostic", bindings={})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [{"record_id": artifact.id,
        "record_type": "artifact", "visibility": "released", "record": artifact.as_dict()}]}))
    workspace = Path(args["workspace"]); doc = json.loads(workspace.read_text())
    doc["sources"][0]["identity"]["value"] = identity(manifest)["sha256"]; workspace.write_text(json.dumps(doc))
    source.update(evidence_ref=make_ref("fixture", artifact.id, artifact.as_dict()), identity_pointer=["source"])
    with pytest.raises(ValueError, match="non-conditioning"): build_input_qualification(bundle, **args)
    source["role"] = role
    report = build_input_qualification(bundle, **args)
    assert role in report["source_verification"][0]["authoritative_roles"]
    source["identity_pointer"] = ["missing"]
    with pytest.raises(ValueError, match="pointer"): build_input_qualification(bundle, **args)


def test_optional_qualification_artifact_records_in_existing_journal(tmp_path):
    from etalon.active import CampaignSpec, CampaignStore, Endpoint
    from etalon.research.journal import ResearchJournal
    bundle, args = make_case(tmp_path, [mol("CC")])
    artifact = write_input_qualification(tmp_path / "qualification.json", bundle, **args)
    store = CampaignStore(tmp_path / "fixture-campaign.sqlite")
    store.configure(CampaignSpec("unmeasured", 1, "credit", "fixture"),
                    [Endpoint("unmeasured", "fixture", "not_measured", "count", "fixture/1", 1, queryable=False)])
    journal = ResearchJournal(store)
    assert journal.record(artifact) == artifact.id
    assert journal.get(artifact.id)["bindings"]["evidence_refs"] == artifact.bindings["evidence_refs"]
    assert not store.observations() and store.balance()["spent"] == 0


def test_sealed_source_is_unavailable(tmp_path):
    bundle, args = make_case(tmp_path, [mol("CC")], roles=["sealed_holdout"])
    with pytest.raises(ValueError, match="unavailable"): build_input_qualification(bundle, **args)


def test_mapping_cap_cannot_be_called_comparable(tmp_path, monkeypatch):
    import etalon.research.input_qualification as module
    monkeypatch.setattr(module, "MAX_MAPPINGS", 1)
    report = qualify(tmp_path, ["c1ccccc1", "c1ccccc1"])
    assert report["transitions"][0]["mapping_limit_reached"]
    assert report["endpoint_eligibility"]["status"] == "ambiguous_atom_correspondence"


def test_unsupported_non_tetrahedral_stereo_cannot_be_called_comparable(tmp_path):
    report = qualify(tmp_path, ["F[As@TB1](Cl)(Br)(I)N", "F[As@TB1](Cl)(Br)(I)N"])
    assert report["stages"][0]["unsupported_stereo_types"] == ["Atom_TrigonalBipyramidal"]
    assert report["endpoint_eligibility"]["status"] == "unsupported_stereo_scope"


def test_missing_or_different_feature_applicability_is_not_comparable(tmp_path, monkeypatch):
    import etalon.research.input_qualification as module
    original = module._inventory
    def changed_inventory(molecule, raw, origin):
        rows, unsupported = original(molecule, raw, origin)
        if origin == "native_output":
            rows[0]["applicability"] = "potential_stereogenic"
        return rows, unsupported
    # Fault-inject a producer/consumer applicability disagreement, without
    # changing graph chemistry. The endpoint must refuse unsupported inference.
    monkeypatch.setattr(module, "_inventory", changed_inventory)
    report = qualify(tmp_path, ["CC(=O)N", "CC(=O)N"])
    assert report["endpoint_eligibility"]["status"] == "unresolved_feature_applicability"


@pytest.mark.parametrize("hidden", [False, True])
def test_transitive_json_payload_refs_preserve_evaluation_role(tmp_path, hidden):
    bundle, args = make_case(tmp_path, [mol("CC")])
    sdf_source = bundle["sources"][0]
    manifest = tmp_path / "manifest.json"
    doc = json.loads(manifest.read_text())
    original_ref = sdf_source["evidence_ref"]
    # A diagnostic container points through another diagnostic JSON payload to
    # an evaluation-only file. Metadata alone would miss that second edge.
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({"role": "sealed_holdout" if hidden else "evaluation_only", "label": None}))
    eval_artifact = Artifact(str(evaluation), **identity(evaluation), source_kind="fixture", role="diagnostic", bindings={})
    middle = tmp_path / "middle.json"
    middle.write_text(json.dumps({"evidence_refs": [make_ref("fixture", eval_artifact.id, eval_artifact.as_dict())]}))
    middle_artifact = Artifact(str(middle), **identity(middle), source_kind="fixture", role="diagnostic", bindings={})
    anchor = tmp_path / "anchor.json"
    anchor.write_text(json.dumps({"source": {k: sdf_source[k] for k in ("sha256", "bytes")},
        "evidence_refs": [original_ref, make_ref("fixture", middle_artifact.id, middle_artifact.as_dict())]}))
    anchor_artifact = Artifact(str(anchor), **identity(anchor), source_kind="fixture", role="diagnostic", bindings={})
    for artifact in [eval_artifact, middle_artifact, anchor_artifact]:
        doc["records"].append({"record_id": artifact.id, "record_type": "artifact", "visibility": "released", "record": artifact.as_dict()})
    manifest.write_text(json.dumps(doc))
    ws = Path(args["workspace"]); w = json.loads(ws.read_text())
    w["sources"][0]["identity"]["value"] = identity(manifest)["sha256"]; ws.write_text(json.dumps(w))
    sdf_source.update(evidence_ref=make_ref("fixture", anchor_artifact.id, anchor_artifact.as_dict()), identity_pointer=["source"])
    with pytest.raises(ValueError, match="non-conditioning|sealed"): build_input_qualification(bundle, **args)


def test_foreign_payload_ref_is_explicitly_unresolved_without_declared_dependency(tmp_path):
    bundle, args = make_case(tmp_path, [mol("CC")])
    source = bundle["sources"][0]
    anchor = tmp_path / "anchor.json"
    anchor.write_text(json.dumps({"source": {k: source[k] for k in ("sha256", "bytes")},
        "evidence_refs": [{**source["evidence_ref"], "source_id": "foreign"}]}))
    artifact = Artifact(str(anchor), **identity(anchor), source_kind="fixture", role="diagnostic", bindings={})
    manifest = tmp_path / "manifest.json"; doc = json.loads(manifest.read_text())
    doc["records"].append({"record_id": artifact.id, "record_type": "artifact", "visibility": "released", "record": artifact.as_dict()})
    manifest.write_text(json.dumps(doc))
    ws = Path(args["workspace"]); w = json.loads(ws.read_text())
    w["sources"][0]["identity"]["value"] = identity(manifest)["sha256"]; ws.write_text(json.dumps(w))
    source.update(evidence_ref=make_ref("fixture", artifact.id, artifact.as_dict()), identity_pointer=["source"])
    with pytest.raises(ValueError, match="unavailable"): build_input_qualification(bundle, **args)


@pytest.mark.parametrize("section,role", [("observations", "evaluation_only"), ("interpretations", "context")])
def test_unbound_bundle_labels_keep_access_restrictions(tmp_path, section, role):
    bundle, args = make_case(tmp_path, [mol("CC")])
    bundle[section] = [{"id": "label", "role": role, "scope": "fixture", "basis_ids": ["0"],
                        "kind": "result", "assumption": "context only", "data": {"label": 42}}]
    with pytest.raises(ValueError, match="non-conditioning"):
        build_input_qualification(bundle, **args, purpose="conditioning")
    artifact = write_input_qualification(tmp_path / "restricted.json", bundle, **args)
    assert artifact.role == "independent_validation"
    assert read_input_qualification(artifact, workspace=args["workspace"])["request"]["bundle"][section]
    with pytest.raises(ValueError, match="non-conditioning"):
        read_input_qualification(artifact, workspace=args["workspace"], purpose="conditioning")
    with pytest.raises(ValueError, match="role"):
        read_input_qualification({**artifact.as_dict(), "role": "diagnostic"}, workspace=args["workspace"])


@pytest.mark.parametrize("visibility", ["released", "sealed", "sealed_holdout", "private"])
def test_wrapper_preserves_fixture_ancestry_and_payload_visibility(tmp_path, visibility):
    bundle, args = make_case(tmp_path, [mol("CC")])
    source = bundle["sources"][0]
    manifest = tmp_path / "manifest.json"; doc = json.loads(manifest.read_text())
    anchor = tmp_path / "anchor.json"
    anchor.write_text(json.dumps({"source": identity(source["path"]), "visibility": visibility,
                                 "evidence_refs": [source["evidence_ref"]]}))
    artifact = Artifact(str(anchor), **identity(anchor), source_kind="existing_real_artifact", role="diagnostic", bindings={})
    doc["records"].append({"record_id": artifact.id, "record_type": "artifact", "visibility": "released", "record": artifact.as_dict()})
    manifest.write_text(json.dumps(doc))
    ws = Path(args["workspace"]); w = json.loads(ws.read_text())
    w["sources"][0]["identity"]["value"] = identity(manifest)["sha256"]; ws.write_text(json.dumps(w))
    source.update(evidence_ref=make_ref("fixture", artifact.id, artifact.as_dict()), identity_pointer=["source"])
    if visibility != "released":
        with pytest.raises(ValueError, match="sealed"):
            build_input_qualification(bundle, **args, purpose="conditioning")
        return
    artifact = write_input_qualification(tmp_path / "derived.json", bundle, **args, purpose="conditioning")
    assert artifact.source_kind == "fixture"
    report = read_input_qualification(artifact, workspace=args["workspace"], purpose="conditioning")
    assert report["source_verification"][0]["authoritative_source_kinds"] == ["existing_real_artifact", "fixture"]
    with pytest.raises(ValueError, match="source kind"):
        read_input_qualification({**artifact.as_dict(), "source_kind": "existing_real_artifact"}, workspace=args["workspace"])


def test_hydrogen_charge_cannot_disappear_into_identity_match(tmp_path):
    params = Chem.SmilesParserParams(); params.removeHs = False
    molecules = [Chem.MolFromSmiles(s, params) for s in ("[H+]C", "[H]C")]
    bundle, args = make_case(tmp_path, molecules)
    with pytest.raises(ValueError, match="charged hydrogen"):
        build_input_qualification(bundle, **args)
