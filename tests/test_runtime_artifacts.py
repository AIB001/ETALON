"""Immutable authored configurations and verified, bounded result inspection."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose
from etalon.data.artifacts import new_run, seal, write_json
from etalon.runtime.artifacts import DESIGN_SCHEMA, MAX_RESPONSE_BYTES, configure, read_result


def design():
    return {"schema": DESIGN_SCHEMA, "name": "caller-selected-properties", "tiers": [
        {"id": "measure", "title": "Requested component", "mode": "serial",
         "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}]}


@pytest.fixture
def snapshot(tmp_path):
    root = new_run(tmp_path, "sealed", {"source": "offline fixture"})
    write_json(root / "records.json", [{"id": number, "smiles": "CCO"} for number in range(7)])
    (root / "library.csv").write_text('id,smiles,note\na,CCO,"two\nlines"\nb,CCN,amine\n', encoding="utf-8")
    (root / "notes.txt").write_text("乙醇\nethanol\n", encoding="utf-8")
    (root / "nested").mkdir()
    write_json(root / "nested" / "summary.json", {"count": 7})
    return seal(root, kind="query", result={"status": "complete"}, infrastructure={"fixture": True})


@pytest.fixture
def screened(tmp_path):
    configuration = configure(tmp_path / "project", design())
    library = tmp_path / "molecules.csv"
    library.write_text("id,smiles\nethanol,CCO\nbenzene,c1ccccc1\nacetate,CC(=O)O\n", encoding="utf-8")
    screen = Screen(tmp_path / "screen")
    run = screen.run(screen.plan(configuration["config_path"], library), run_id="real-cpu")
    assert run.status == "SUCCEEDED" and not run.failed
    artifact = next(stage.artifact_id for stage in run.stages if stage.stage_id == "properties")
    return {"workspace": str(screen.workspace), "run": run.as_dict()}, artifact


def test_design_and_full_configuration_share_immutable_identity_without_added_tiers(tmp_path):
    authored = design()
    before = copy.deepcopy(authored)
    first = configure(tmp_path / "project", authored)
    second = configure(tmp_path / "project", first["configuration"])
    assert first == second
    assert authored == before
    assert Path(first["config_path"]).name == first["config_id"] + ".json"
    assert json.loads(Path(first["config_path"]).read_text()) == first["configuration"]
    assert [tier["id"] for tier in first["configuration"]["tiers"]] == ["measure"]
    assert first["configuration"]["finalize"]["steps"] == []
    assert first["configuration"]["standardize"]["settings"]["identity_policy"]["tautomer_policy"] == "preserve"


def test_concurrent_configuration_publication_is_idempotent(tmp_path):
    authored = design()
    with ThreadPoolExecutor(max_workers=3) as workers:
        results = list(workers.map(lambda _: configure(tmp_path / "project", authored), range(3)))
    assert results[0] == results[1] == results[2]
    assert list((tmp_path / "project" / "configurations").iterdir()) == [Path(results[0]["config_path"])]


@pytest.mark.parametrize("configuration", [
    {"name": "ambiguous", "tiers": []},
    {"schema": DESIGN_SCHEMA, "name": "empty", "tiers": []},
    {"schema": DESIGN_SCHEMA, "name": "unknown", "tiers": [], "shell": "echo unsafe"},
])
def test_invalid_configuration_does_not_create_workspace(tmp_path, configuration):
    with pytest.raises(ValueError):
        configure(tmp_path / "absent", configuration)
    assert not (tmp_path / "absent").exists()


def test_changed_configuration_file_is_never_overwritten(tmp_path):
    authored = design()
    first = configure(tmp_path, authored)
    path = Path(first["config_path"])
    changed = {"kind": "not-the-original"}
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="immutable identity"):
        configure(tmp_path, authored)
    assert json.loads(path.read_text()) == changed


def test_configuration_directory_and_file_symlinks_cannot_redirect_publication(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "configurations").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        configure(workspace, design())
    assert not list(outside.iterdir())
    (workspace / "configurations").unlink()
    authored = design()
    result = configure(workspace, authored)
    path = Path(result["config_path"])
    path.unlink()
    target = outside / "untouched.json"
    target.write_text("{}", encoding="utf-8")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        configure(workspace, authored)
    assert target.read_text() == "{}"


def test_result_pages_and_json_pointer_preserve_values_and_escape_keys():
    result = {"a/b": {"~key": [{"id": number} for number in range(5)]}, "status": "complete"}
    first = read_result(result, member="/a~1b/~0key", limit=2)
    assert first["data"] == [{"id": 0}, {"id": 1}]
    assert (first["total"], first["returned"], first["next_offset"]) == (5, 2, 2)
    final = read_result(result, member="/a~1b/~0key", offset=4, limit=2)
    assert final["data"] == [{"id": 4}] and final["next_offset"] is None
    scalar = read_result(result, member="/a~1b/~0key/3/id")
    assert scalar["data"] == 3 and scalar["unit"] == "values"
    with pytest.raises(KeyError):
        read_result(result, member="/a~1b/~0key/9")
    with pytest.raises(ValueError, match="Pointer"):
        read_result(result, member="a/b")
    with pytest.raises(ValueError, match="escape"):
        read_result(result, member="/a~2b")


@pytest.mark.parametrize(("offset", "limit"), [(-1, 1), (True, 1), (0, 0), (0, True), (0, 1001)])
def test_pagination_refuses_invalid_bounds(offset, limit):
    with pytest.raises(ValueError, match="integer"):
        read_result({}, offset=offset, limit=limit)


def test_pagination_at_end_is_empty_but_beyond_end_is_refused():
    result = {"rows": [1, 2]}
    page = read_result(result, member="/rows", offset=2)
    assert page["data"] == [] and page["next_offset"] is None
    with pytest.raises(ValueError, match="exceeds total"):
        read_result(result, member="/rows", offset=3)


def test_response_size_shrinks_page_and_reports_next_offset():
    rows = [{"id": number, "payload": "x" * 550_000} for number in range(3)]
    page = read_result({"rows": rows}, member="/rows", limit=3)
    assert page["data"] == rows[:1]
    assert page["returned"] == 1 and page["next_offset"] == 1 and page["size_limited"]
    assert len(json.dumps({"ok": True, "page": page}, indent=2, ensure_ascii=False).encode()) < MAX_RESPONSE_BYTES
    with pytest.raises(ValueError, match="one result item"):
        read_result({"rows": rows})


def test_snapshot_lists_verified_members_and_reads_json_csv_and_unicode_text(snapshot):
    inventory = read_result(snapshot, kind="snapshot", limit=2)
    assert inventory["format"] == "manifest" and inventory["total"] == 5
    assert inventory["next_offset"] == 2
    records = read_result(snapshot, kind="snapshot", member="records.json", offset=2, limit=3)
    assert [row["id"] for row in records["data"]] == [2, 3, 4]
    assert records["total"] == 7 and records["next_offset"] == 5
    csv_page = read_result(snapshot, kind="snapshot", member="library.csv", limit=1)
    assert csv_page["data"] == [{"id": "a", "smiles": "CCO", "note": "two\nlines"}]
    text = read_result(snapshot, kind="snapshot", member="notes.txt", limit=2)
    assert text["data"] == "乙醇" and text["unit"] == "characters" and text["next_offset"] == 2
    nested = read_result(snapshot, kind="snapshot", member="nested/summary.json")
    assert nested["data"] == {"count": 7}


@pytest.mark.parametrize("member", ["../outside.txt", "/etc/passwd", "nested/../../secret",
                                   "C:\\secret.txt", "C:secret.txt", "nested\\secret.txt",
                                   "./records.json", "nested//summary.json"])
def test_snapshot_member_cannot_escape_its_manifest(snapshot, member):
    with pytest.raises(ValueError, match="relative path"):
        read_result(snapshot, kind="snapshot", member=member)


def test_snapshot_unlisted_member_is_refused(snapshot):
    with pytest.raises(KeyError, match="not listed"):
        read_result(snapshot, kind="snapshot", member="snapshot.json")


def test_snapshot_tampering_is_checked_even_when_reading_another_member(snapshot):
    root = Path(snapshot["snapshot"])
    (root / "records.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        read_result(snapshot, kind="snapshot", member="notes.txt")


def test_snapshot_member_and_manifest_symlinks_are_refused(snapshot, tmp_path):
    root = Path(snapshot["snapshot"])
    original = (root / "records.json").read_bytes()
    outside = tmp_path / "outside.json"
    outside.write_bytes(original)
    (root / "records.json").unlink()
    (root / "records.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        read_result(snapshot, kind="snapshot", member="records.json")
    (root / "records.json").unlink()
    (root / "records.json").write_bytes(original)
    manifest = (root / "snapshot.json").read_bytes()
    outside.write_bytes(manifest)
    (root / "snapshot.json").unlink()
    (root / "snapshot.json").symlink_to(outside)
    with pytest.raises(ValueError, match="manifest.*symlink"):
        read_result(snapshot, kind="snapshot")


def test_snapshot_root_symlink_is_refused(snapshot, tmp_path):
    link = tmp_path / "alias"
    link.symlink_to(snapshot["snapshot"], target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        read_result({"snapshot": str(link)}, kind="snapshot")


def test_screen_result_reads_real_cpu_properties_by_contract(screened):
    result, artifact_id = screened
    first = read_result(result, kind="screen", artifact_id=artifact_id, contract_id="property/v1", limit=2)
    final = read_result(result, kind="screen", artifact_id=artifact_id, contract_id="property/v1", offset=2)
    rows = first["data"] + final["data"]
    assert first["total"] == 3 and first["next_offset"] == 2 and final["next_offset"] is None
    assert sorted(row["mw"] for row in rows) == pytest.approx([46.069, 60.052, 78.114], abs=0.02)
    assert all(row["_contract"] == "property/v1" for row in rows)


def test_screen_cannot_read_an_artifact_not_recorded_in_the_node(screened):
    result, artifact_id = screened
    filtered = {**result, "run": {"stages": []}}
    with pytest.raises(ValueError, match="recorded stage"):
        read_result(filtered, kind="screen", artifact_id=artifact_id)
    with pytest.raises(KeyError, match="no port"):
        read_result(result, kind="screen", artifact_id=artifact_id, contract_id="unknown/v1")


def test_screen_uses_digest_verification_before_exposing_rows(screened):
    from molcascade.artifacts.store import LocalArtifactStore
    from molcascade.errors import ArtifactIntegrityError

    result, artifact_id = screened
    directory = LocalArtifactStore(result["workspace"]).artifact_directory(artifact_id, verify=True)
    parquet = next(directory.rglob("*.parquet"))
    with parquet.open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        read_result(result, kind="screen", artifact_id=artifact_id, contract_id="property/v1")


def test_screen_read_does_not_create_an_unknown_workspace(tmp_path):
    missing = tmp_path / "not-created"
    with pytest.raises(FileNotFoundError):
        read_result({"workspace": str(missing), "run": {"stages": [{"artifact_id": "recorded"}]}},
                    kind="screen", artifact_id="recorded")
    assert not missing.exists()


def test_raw_cascade_validation_preserves_authored_scientific_configuration(tmp_path):
    raw = compose("custom", design()["tiers"])
    raw["tiers"][0]["criteria"][0]["gate"] = {"backend": "not-a-versioned-plugin"}
    with pytest.raises(ValueError):
        configure(tmp_path / "absent", raw)
    assert not (tmp_path / "absent").exists()
