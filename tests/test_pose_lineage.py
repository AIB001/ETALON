"""Real RDKit/Arrow processing of labelled engine fixtures; no GPU or affinity claim."""
from copy import deepcopy
from dataclasses import replace
import csv
import gzip
import json
import shutil

import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from rdkit import Chem
from rdkit.Chem import AllChem

from etalon.boundary.infra import load

load("molcascade")

from molcascade.contracts import DOCKING_SCORE_V1, PARENT_V1, POSE_LINEAGE_V1, validate_table
from molcascade.parallel import ShardOutcome, ShardTask
from molcascade.parallel.shards import _write_checkpoint
from molcascade.plugins.builtin.docking import pose_quality as quality
from molcascade.plugins.builtin.docking import unidock
from molcascade.plugins.api import PendingOutput, StageResponse
from molcascade.plugins.manifest import PluginKind
from molcascade.plugins.registry import create_builtin_registry
from molcascade.runtime.runner import LocalRunner
from etalon.screening_delivery import _pose_tags, export_screen_delivery


def _molblock():
    molecule = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    assert AllChem.EmbedMolecule(molecule, randomSeed=41) == 0
    return Chem.MolToMolBlock(molecule)


def _row(block, *, parent="fixture-a", rank=0, score=-7.0):
    return dict(parent_id=parent, engine_id="unidock", receptor_id="fixture-receptor",
                method_id="fixture-method", pose_rank=rank, score=score,
                score_kind="VINA_KCAL_MOL", direction="LOWER_STRONGER", secondary_score=None,
                pose_molblock=block)


def _checked_fixture(root):
    """Native pose/score rows are fixtures; split/check/checkpoint/join are actual code."""
    root.mkdir(parents=True, exist_ok=True)
    receptor = Chem.MolFromMolBlock(_molblock(), removeHs=False)
    conformer = receptor.GetConformer()
    for atom in range(receptor.GetNumAtoms()):
        position = conformer.GetAtomPosition(atom)
        # A deliberately overlapping receptor fixture makes actual MMFF repair
        # move the ligand, so raw and delivered identities are distinguishable.
        conformer.SetAtomPosition(atom, (position.x + 2.5, position.y + 1.25, position.z + 0.75))
    receptor_path = root / "receptor.pdb"
    Chem.MolToPDBFile(receptor, str(receptor_path))
    config = unidock.UniDockConfig(
        receptor_path=str(receptor_path), receptor_pdbqt_path=str(root / "unused.pdbqt"),
        center_x=0.0, center_y=0.0, center_z=0.0, size_x=20.0, size_y=20.0, size_z=20.0,
        quality_chunk_rows=1, pose_quality=quality.PoseQualityConfig(checks=()),
    )
    parents = [dict(parent_id=p, identity_policy_id="fixture", parent_smiles="CCO",
                    registration_key=p, stereo_key=None, formula="C2H6O", duplicate_count=1)
               for p in ("fixture-a", "fixture-b")]
    raw = [_row("labelled unparseable pose", rank=0, score=-9.0),
           _row(_molblock(), rank=1, score=-8.0), _row(_molblock(), parent="fixture-b")]
    produced_dir = root / "produced" / "shard-00000"
    final_dir = root / "joined" / "shard-00000"
    produced_dir.mkdir(parents=True)
    final_dir.mkdir(parents=True)
    produced_files = {port: produced_dir / (port + ".parquet") for port in ("primary", "scores", "lineage")}
    pq.write_table(pa.Table.from_pylist(parents, schema=PARENT_V1.schema), produced_files["primary"])
    pq.write_table(pa.Table.from_pylist(raw, schema=DOCKING_SCORE_V1.schema), produced_files["scores"])
    pq.write_table(pa.Table.from_pylist([], schema=POSE_LINEAGE_V1.schema), produced_files["lineage"])
    produced = ShardTask(0, (produced_files["primary"],), 0, len(parents), produced_files,
                         config=config.model_dump(mode="json"))
    _write_checkpoint(produced_dir.parent, 0, ShardOutcome(2, {"primary": 2, "scores": 3, "lineage": 0}))
    final = replace(produced, output_paths={port: final_dir / (port + ".parquet") for port in produced_files})
    chunks = list(unidock._split_quality(final, produced))
    assert len(chunks) == 2
    for chunk in chunks:
        outcome = unidock._check_quality_chunk(chunk)
        _write_checkpoint(next(iter(chunk.output_paths.values())).parent.parent, chunk.index, outcome)
    outcome = unidock._join_quality(final, produced, chunks)
    assert outcome.rows_out == {"primary": 2, "scores": 2, "lineage": 3}
    assert pq.read_table(produced_files["scores"]).to_pylist() == raw
    return final.output_paths, raw, config


def test_real_repair_preserves_original_score_identity_and_surviving_rank(tmp_path):
    paths, raw, config = _checked_fixture(tmp_path)
    scores = pq.read_table(paths["scores"]).to_pylist()
    lineage = pq.read_table(paths["lineage"]).to_pylist()
    validate_table(pq.read_table(paths["lineage"]), POSE_LINEAGE_V1)
    validate_table(pq.read_table(paths["scores"]), DOCKING_SCORE_V1)
    dropped, kept, _ = lineage
    assert dropped["qc_state"] == "fail" and not dropped["retained"]
    assert dropped["selected_pose_rank"] is None and dropped["delivered_pose_sha256"] is None
    assert (kept["raw_pose_rank"], kept["selected_pose_rank"]) == (1, 0)
    assert scores[0]["score"] == -8.0 and scores[0]["pose_rank"] == 0
    assert kept["repair_state"] == "completed" and kept["repair_displacement"] > 0
    assert kept["qc_state"] == "unknown"  # No PoseBusters checks were requested.
    assert kept["raw_pose_sha256"] == kept["score_pose_sha256"] == quality.pose_bytes_sha256(raw[1]["pose_molblock"])
    assert kept["delivered_pose_sha256"] == quality.pose_bytes_sha256(scores[0]["pose_molblock"])
    assert kept["delivered_pose_sha256"] != kept["raw_pose_sha256"]
    # Existing quality calls receive the exact same scores, ranks and coordinates.
    legacy = quality.PoseQualitySession(config.pose_quality, receptor_path=config.receptor_path)
    assert legacy.apply(deepcopy(raw), keep_poses=True) == scores


def test_qc_states_do_not_add_filters_or_guess_score_provenance(tmp_path, monkeypatch):
    block = _molblock()
    monkeypatch.setattr(quality.PoseQualitySession, "_receptor_molecule", lambda _: None)
    # Explicit verdict fixture tests transport of pass/fail, not PoseBusters accuracy.
    monkeypatch.setattr(quality, "judge_poses", lambda *a, **k: [{"bond_lengths": False}, {"bond_lengths": True}])
    session = quality.PoseQualitySession(quality.PoseQualityConfig(
        relax=False, discard_failing=False, checks=("bond_lengths",)), receptor_path=str(tmp_path / "unused.pdb"))
    sidecar = []
    kept = session.apply([_row(block), _row(block, rank=1)], keep_poses=True, lineage=sidecar)
    assert len(kept) == 2 and [r["qc_state"] for r in sidecar] == ["fail", "pass"]
    assert all(r["retained"] and r["score_basis"] == "unknown" and r["score_pose_sha256"] is None for r in sidecar)
    disabled = quality.PoseQualitySession(quality.PoseQualityConfig(enabled=False), receptor_path="unused")
    sidecar = []
    assert disabled.apply([_row(None)], keep_poses=False, lineage=sidecar)[0]["score"] == -7.0
    assert sidecar[0]["qc_state"] == "unknown" and sidecar[0]["raw_pose_sha256"] is None


def test_production_checkpoint_retains_unmodified_engine_fixture_for_quality(tmp_path, monkeypatch):
    paths, raw, config = _checked_fixture(tmp_path / "inputs")
    directory = tmp_path / "production" / "shard-00000"
    directory.mkdir(parents=True)
    outputs = {port: directory / (port + ".parquet") for port in paths}
    task = ShardTask(0, (paths["primary"],), 0, 2, outputs, config=config.model_dump(mode="json"))
    # Only native engine/preparation boundaries are fixtures. The production
    # reader/writers and the checkpoint digest calculation execute normally.
    monkeypatch.setattr(unidock, "require_gpu_lane", lambda *a, **kw: None)
    monkeypatch.setattr(unidock, "require_meeko", lambda **kw: None)
    monkeypatch.setattr(unidock, "resolved_executable", lambda *a, **kw: "fixture executable")
    monkeypatch.setattr(unidock, "_prepared_receptor", lambda c: (tmp_path / "fixture.pdbqt", "fixture"))
    monkeypatch.setattr(unidock, "shard_geometry", lambda task, parents, **kw: {p: _molblock() for p in parents})
    monkeypatch.setattr(unidock, "ligand_pdbqt", lambda block: "labelled ligand PDBQT fixture")
    calls = []

    def engine_rows(ligands, **kwargs):
        calls.append([p for p, _ in ligands])
        return deepcopy(raw), 0

    monkeypatch.setattr(unidock, "_dock_batch", engine_rows)
    outcome = unidock._produce_shard(task)
    _write_checkpoint(directory.parent, 0, outcome)
    assert calls == [["fixture-a", "fixture-b"]]
    assert outcome.rows_out == {"primary": 2, "scores": 3, "lineage": 0}
    assert pq.read_table(outputs["scores"]).to_pylist() == raw
    validate_table(pq.read_table(outputs["lineage"]), POSE_LINEAGE_V1)


@pytest.mark.parametrize("with_lineage", [True, False])
def test_committed_pose_sidecar_exports_scores_and_coordinates_without_recomputation(tmp_path, with_lineage):
    paths, _, _ = _checked_fixture(tmp_path / "fixture")
    ports = {"primary": PARENT_V1.id, "scores": DOCKING_SCORE_V1.id}
    if with_lineage:
        ports["lineage"] = POSE_LINEAGE_V1.id

    class FixtureSource:
        descriptor = unidock.LineageUniDockPlugin.descriptor.model_copy(update={
            "id": "fixture.pose_rows", "version": "0.1.0", "kind": PluginKind.SOURCE,
            "inputs": (), "outputs": tuple(ports.values()), "output_ports": ports,
        })

        def execute(self, request, context):
            for port in ports:
                shutil.copyfile(paths[port], context.staging_root / (port + ".parquet"))
            return StageResponse({port: PendingOutput(contract, (port + ".parquet",)) for port, contract in ports.items()})

    registry = create_builtin_registry()
    registry.register(FixtureSource(), origin="labelled test fixture", trusted=True)
    runner = LocalRunner(tmp_path / "work", plugins=registry)
    state = runner.run({"schema_version": 1, "name": "fixture delivery", "stages": [
        {"id": "poses", "slot": "source", "plugin": "fixture.pose_rows@0.1.0", "config": {}}
    ]}, run_id="fixture")
    assert str(state.status) == "SUCCEEDED", state.error
    output = tmp_path / "delivery"
    report = export_screen_delivery(runner.store.root, "fixture", output)
    poses = list(Chem.SDMolSupplier(str(next((output / "poses").glob("*-dock.sdf"))), removeHs=False))
    scores = pq.read_table(paths["scores"]).to_pylist()
    assert len(poses) == 2 and report["scientific_calls"] == 0
    assert report["stages"][0]["counts"]["dock_top1"] == 2
    for mol, row in zip(poses, scores, strict=True):
        original = Chem.MolFromMolBlock(row["pose_molblock"], removeHs=False)
        assert (mol.GetConformer().GetPositions() == original.GetConformer().GetPositions()).all()
        assert float(mol.GetProp("score")) == row["score"]
        assert mol.GetProp("qc_state") == "unknown"
    if with_lineage:
        assert poses[0].GetProp("raw_pose_rank") == "1"
        assert poses[0].GetProp("selected_pose_rank") == "0"
        assert poses[0].GetProp("score_recomputed_after_repair") == "False"
        with gzip.open(next((output / "poses").glob("*-lineage.csv.gz")), "rt", encoding="utf-8") as stream:
            records = list(csv.DictReader(stream))
        assert len(records) == 3 and records[0]["qc_state"] == "fail"
        assert poses[0].GetProp("score_pose_sha256") != poses[0].GetProp("delivered_pose_sha256")
    else:
        assert all(mol.GetProp("pose_lineage") == "unavailable" for mol in poses)
        assert not poses[0].HasProp("raw_pose_rank")
    assert export_screen_delivery(runner.store.root, "fixture", output)["reused"]
    if not with_lineage:
        # A completed old-version export stays readable and is never rewritten.
        from etalon.active.schema import digest
        from etalon.runtime.files import file_identity

        old_key = digest({"version": 4, "run": "fixture", "revision": report["revision_id"],
                          "artifacts": [("poses", report["stages"][0]["artifact_id"])],
                          "status": report["run_status"], "threshold": 2.0, "locations": {}})
        marker = output / "request.json"
        marker.write_text(json.dumps({"request_key": old_key, "run_id": "fixture"}), encoding="utf-8")
        report["request_key"] = old_key
        report["files"]["request.json"] = file_identity(marker)
        receipt = output / "delivery.json"
        receipt.write_text(json.dumps(report), encoding="utf-8")
        before = receipt.read_bytes()
        assert export_screen_delivery(runner.store.root, "fixture", output)["reused"]
        assert receipt.read_bytes() == before


def test_delivery_does_not_attach_another_pose_quality(tmp_path):
    paths, _, _ = _checked_fixture(tmp_path)
    row = pq.read_table(paths["scores"]).to_pylist()[0]
    sidecar = pq.read_table(paths["lineage"]).to_pylist()[1]
    assert _pose_tags(row, sidecar)["pose_lineage"] == "matched"
    row["pose_molblock"] += "\n"
    tags = _pose_tags(row, sidecar)
    assert tags["pose_lineage"] == "identity_mismatch" and tags["qc_state"] == "unknown"
    assert "raw_pose_rank" not in tags


def test_lineage_plugin_is_explicit_and_defaults_remain_v04():
    from molcascade.backends.catalog import BUILTIN_BACKEND_SPECS
    from molcascade.backends.preflight import _BY_PLUGIN_REF
    from molcascade.errors import PluginError

    registry = create_builtin_registry()
    assert registry.get("docking.unidock@0.5.0").descriptor.output_ports["lineage"] == POSE_LINEAGE_V1.id
    for version in ("0.2.0", "0.3.0", "0.4.0"):
        assert "lineage" not in registry.get("docking.unidock@" + version).descriptor.output_ports
    with pytest.raises(PluginError, match="specify a version"):
        registry.get("docking.unidock")
    assert next(s for s in BUILTIN_BACKEND_SPECS if s.id == "unidock").plugin_ref == "docking.unidock@0.4.0"
    assert _BY_PLUGIN_REF["docking.unidock@0.5.0"] == _BY_PLUGIN_REF["docking.unidock@0.4.0"]
