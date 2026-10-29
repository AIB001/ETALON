"""Real local chemistry on labelled fixtures; no GPU generation or affinity claims."""
import json
import subprocess
import sys

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from etalon.generate.feedback import generation_feedback
from etalon.generate.handoff import prepare_handoff
from etalon.runtime.files import file_identity


@pytest.fixture
def generated(tmp_path):
    candidates = []
    for i, smiles in enumerate(("CCO", "CCN", "CCCC")):
        mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
        assert AllChem.EmbedMolecule(mol, randomSeed=17) == 0
        path = tmp_path / f"candidate-{i}.sdf"
        mol.SetProp("native_property", "retain value and SD framing")
        mol.SetProp("ETALON_PARTITION", "labelled obsolete handoff tag")
        with Chem.SDWriter(str(path)) as writer:
            writer.write(mol)
        candidates.append({"candidate_id": str(i), "path": str(path), "canonical_smiles": smiles,
            "quality": {"plausibility": {"heavy_atoms": mol.GetNumHeavyAtoms(), **({"h_bond_donors": 1} if i < 2 else {})}},
            "audit_status": "fixture"})
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"request_hash": "fixture", "pocket": {"source": "fixture-frame"},
        "models": {"fixture": {"requested": 4, "generated": 3, "status": "PARTIAL", "candidates": candidates}}}))
    return path, candidates


def test_feedback_unknown_is_not_pass_and_duplicate_manifests_not_counted(generated):
    path, _ = generated
    report = generation_feedback([path, path], {"heavy_atoms": {"min": 3}, "h_bond_donors": {"max": 1}})
    assert len(report["runs"]) == 1
    row = report["runs"][0]
    assert (row["within_window"], row["outside_window"], row["unknown"]) == (2, 0, 1)
    assert row["requested"] == 4 and row["candidate_records"] == 3
    assert report["generation_calls"] == 0


def test_handoff_preserves_pose_and_replay_does_not_parse_again(generated, tmp_path, monkeypatch):
    path, candidates = generated
    result = prepare_handoff([path], tmp_path / "handoff", operation="inspect_pose")
    molecules = list(Chem.SDMolSupplier(result["outputs"]["sdf"]["path"], removeHs=False))
    for molecule, row in zip(molecules, candidates):
        original = Chem.MolFromMolFile(row["path"], removeHs=False)
        assert (molecule.GetConformer().GetPositions() == original.GetConformer().GetPositions()).all()
        assert molecule.GetProp('native_property')=='retain value and SD framing'
        assert json.loads(molecule.GetProp('ETALON_PARTITION'))['model']=='fixture'
    assert result["scientific_calls"] == 0 and result["candidate_count"] == 3
    monkeypatch.setattr(Chem, "MolFromMolBlock", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reparsed")))
    assert prepare_handoff([path], tmp_path / "handoff", operation="inspect_pose")["reused"]
    with pytest.raises(ValueError, match="another selection"):
        prepare_handoff([path], tmp_path / "handoff", operation="redock")
    with open(candidates[0]["path"], "a") as out:
        out.write("changed")
    with pytest.raises(ValueError, match="source changed"):
        prepare_handoff([path], tmp_path / "handoff", operation="inspect_pose")


def test_native_method_request_runs_and_feedback_remains_available(generated, tmp_path):
    path, _ = generated
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    request.write_text(json.dumps({"inputs": {"manifests": [str(path)], "criteria": {"heavy_atoms": {"min": 3}}},
        "outputs": {"feedback": str(tmp_path / "feedback.json")}}))
    subprocess.run([sys.executable, "-m", "etalon.generate.feedback", "--request", str(request), "--result", str(result)], check=True)
    body = json.loads(result.read_text())
    assert body["schema"] == "etalon-method-result/1" and body["feedback"]["generation_calls"] == 0
    assert file_identity(body["outputs"]["feedback"])["bytes"] > 0


def test_relocated_manifest_consumes_real_originals(generated, tmp_path):
    path, candidates = generated
    body = json.loads(path.read_text())
    for row in body["models"]["fixture"]["candidates"]:
        row["path"] = "/original-host/" + row["path"].replace("\\", "/").split("/")[-1]
    path.write_text(json.dumps(body))
    result = prepare_handoff([path], tmp_path / "moved", operation="properties", locations={"/original-host": str(tmp_path)})
    assert result["candidate_count"] == 3


def test_same_chemical_identity_keeps_model_run_partitions(generated, tmp_path):
    path, _ = generated
    body = json.loads(path.read_text())
    body['models']['second-model'] = body['models']['fixture']
    path.write_text(json.dumps(body))
    result = prepare_handoff([path], tmp_path/'partitions', operation='properties', include_scaffolds=True)
    rows = json.loads(open(result['outputs']['candidates']['path']).read())['selection']
    assert len(rows)==6 and len({r['id'] for r in rows})==6
    assert {r['partition']['model'] for r in rows}=={'fixture','second-model'}
    assert all('murcko_smiles' in r['partition'] for r in rows)
    # Follow the returned input fragment through native registration and a gate.
    # No campaign-specific prepare_generated.py or first-model-wins deduplication.
    from etalon.boundary.screen import Screen
    from etalon.campaign.design import component, compose
    from etalon.screening_delivery import export_screen_delivery
    import csv
    import gzip
    config=compose('generated-native-chain',[{'id':'window','title':'window',
        'criteria':[component('mw','chemistry.rdkit_property_range_gate@0.1.0',settings={'mw_min':50})]}])
    config.update(result['screen_input'])
    config_path=tmp_path/'screen.json';config_path.write_text(json.dumps(config))
    screen=Screen(tmp_path/'screen')
    run=screen.run(screen.plan(config_path,result['outputs']['sdf']['path']),run_id='generated')
    assert run.status=='SUCCEEDED'
    output=tmp_path/'delivered'
    export_screen_delivery(screen.workspace,'generated',output)
    with gzip.open(output/'parent-origins.csv.gz','rt') as f:origins=list(csv.DictReader(f))
    assert len(origins)==6 and len({r['parent_id'] for r in origins})==3
    parts=[json.loads(json.loads(r['source_metadata_json'])['sdf_properties']['ETALON_PARTITION']) for r in origins]
    assert {p['model'] for p in parts}=={'fixture','second-model'}
    assert all('murcko_smiles' in p and p['conditioning_ref']=='/pocket' for p in parts)
    # A new tier reuses registered chemistry in another workspace. Resolve only
    # explicit origin references instead of copying histories or recomputing it.
    registered=next(s for s in run.stages if s.stage_id=='standardize').artifact_id
    survivors={r['parent_id'] for r in screen.read(run.stages[-1].artifact_id,contract_id='parent/v1')}
    selection=tmp_path/'selected.tsv'
    selection.write_text('parent_id\n'+'\n'.join(sorted(survivors))+'\n')
    continuation=compose('reuse',[{'id':'measure','title':'measure',
        'criteria':[component('props','features.rdkit_properties@0.1.0')]}])
    continuation['library']={'format':'registered','path':str(screen.workspace)}
    continuation['ingest']={'id':'registered','backend':'source.registered_parents@0.1.0','settings':{
        'schema_version':1,'artifact_id':registered,
        'identity_policy':config['standardize']['settings']['identity_policy'],
        'selection_path':str(selection),'selection_sha256':file_identity(selection)['sha256']}}
    continuation['standardize']=None
    next_config=tmp_path/'next.json';next_config.write_text(json.dumps(continuation))
    next_screen=Screen(tmp_path/'next-screen')
    next_run=next_screen.run(next_screen.plan(next_config),run_id='reused')
    assert next_run.status=='SUCCEEDED'
    assert not any(s.plugin.startswith('chemistry.rdkit_standardize') for s in next_run.stages)
    receipt=export_screen_delivery(next_screen.workspace,'reused',tmp_path/'next-delivery')
    assert receipt['source_coverage']['unresolved_origins']==[]
    with gzip.open(tmp_path/'next-delivery/parent-origins.csv.gz','rt') as f:next_origins=list(csv.DictReader(f))
    assert len(next_origins)==2
    assert next_origins==[r for r in origins if r['parent_id'] in survivors]
