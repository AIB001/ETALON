"""Legacy execution contracts, tested with simulated subprocesses, never PRISM jobs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from etalon.boundary import simulate as module
from etalon.boundary.simulate import (
    DriveRecord,
    Environment,
    Simulate,
    SimulationError,
    StageStatus,
    Warnings,
    read_binding_energy,
)
from etalon.campaign.expensive import PrismStage


@pytest.fixture
def simulation(tmp_path, monkeypatch):
    """The fake creates only three tiny files; no scientific tools are imported/run."""
    calls = []
    provenance = {"name": "prism", "source_commit": "simulated-fixture", "pinned": True}
    infra = SimpleNamespace(import_root=tmp_path, provenance=lambda: dict(provenance))
    runner = Simulate(tmp_path / "workspace", Environment(Path("fixture-python"), Path("fixture-gmx")), infra=infra)
    protein, ligand = tmp_path / "protein.pdb", tmp_path / "ligand.sdf"
    protein.write_text("fixture protein")
    ligand.write_text("fixture ligand")

    def run(command, **kwargs):
        calls.append(command)
        if command[0] != "bash":
            md = Path(command[5]) / "GMX_PROLIG_MD"
            md.mkdir()
            for name in ("topol.top", "solv_ions.gro", "localrun.sh"):
                (md / name).write_text("simulation fixture only")
        return module._CapturedRun(0, "simulated output", "")

    monkeypatch.setattr(module, "_run_owned", run)
    return runner, protein, ligand, calls, provenance


@pytest.mark.parametrize("run_id", ["", " ", ".", "..", "../victim", "/tmp/victim", "a/b", "a\\b", "a\0b", 3])
def test_unsafe_run_identity_never_reaches_delete_or_execution(simulation, monkeypatch, run_id):
    runner, protein, ligand, calls, _ = simulation
    monkeypatch.setattr(module.shutil, "rmtree", lambda *_: pytest.fail("unsafe deletion"))
    with pytest.raises(SimulationError, match="run_id"):
        runner.build(protein, ligand, run_id=run_id, overwrite=True)
    assert not calls
    assert protein.read_text() == "fixture protein"


@pytest.mark.parametrize("dangling", [False, True])
def test_run_directory_symlink_cannot_be_reused_or_overwritten(simulation, tmp_path, dangling):
    runner, protein, ligand, calls, _ = simulation
    victim = tmp_path / "victim"
    if not dangling:
        victim.mkdir()
        (victim / "preserved").write_text("preserved")
    (runner.workspace / "r").symlink_to(victim, target_is_directory=True)
    with pytest.raises(SimulationError, match="symlink"):
        runner.build(protein, ligand, run_id="r", overwrite=True)
    assert not calls
    if not dangling:
        assert (victim / "preserved").read_text() == "preserved"


def test_overwrite_does_not_delete_its_own_scientific_input(simulation):
    runner, protein, ligand, calls, _ = simulation
    run = runner.workspace / "r"
    run.mkdir()
    nested = run / "protein.pdb"
    nested.write_bytes(protein.read_bytes())
    with pytest.raises(SimulationError, match="input files"):
        runner.build(nested, ligand, run_id="r", overwrite=True)
    assert nested.is_file() and not calls


def test_matching_build_reuses_without_any_new_subprocess(simulation):
    runner, protein, ligand, calls, _ = simulation
    first = runner.build(protein, ligand, run_id="r")
    second = runner.build(protein, ligand, run_id="r", reuse=True)
    assert second.built and second.arguments == first.arguments
    assert len(calls) == 1


def test_explicit_production_duration_reaches_prism_and_is_part_of_reuse_identity(simulation):
    runner, protein, ligand, calls, _ = simulation
    first = runner.build(protein, ligand, run_id="duration", production_ns=0.1)
    spec = json.loads(calls[0][6])
    assert ["simulation", "production_time_ns", 0.1] in spec["config"]
    assert first.arguments["config_overrides"]["simulation.production_time_ns"] == 0.1
    runner.build(protein, ligand, run_id="duration", production_ns=0.1, reuse=True)
    assert len(calls) == 1
    with pytest.raises(SimulationError, match="could not be reused"):
        runner.build(protein, ligand, run_id="duration", production_ns=1, reuse=True)
    assert len(calls) == 1


@pytest.mark.parametrize("duration", [True, 0, -1, float("nan"), float("inf"), "0.1"])
def test_invalid_production_duration_never_launches_a_build(simulation, duration):
    runner, protein, ligand, calls, _ = simulation
    with pytest.raises(SimulationError, match="production_ns"):
        runner.build(protein, ligand, run_id="duration", production_ns=duration)
    with pytest.raises(ValueError, match="production_ns"):
        PrismStage(runner, protein, production_ns=duration)
    assert not calls


def test_legacy_stage_forwards_explicit_production_duration(simulation):
    runner, protein, ligand, calls, _ = simulation
    measurement = PrismStage(runner, protein, production_ns=0.25)._one("a", ligand, None)
    assert ["simulation", "production_time_ns", 0.25] in json.loads(calls[0][6])["config"]
    assert measurement.expensive_value is None  # Fake subprocess files are not affinity evidence.


@pytest.mark.parametrize("kwargs", [
    {"forcefield": "different"}, {"ligand_forcefield": "different"},
    {"protonation": "different"}, {"gaussian_method": "hf"},
])
def test_reuse_refuses_changed_scientific_protocol_without_spending(simulation, kwargs):
    runner, protein, ligand, calls, _ = simulation
    first = runner.build(protein, ligand, run_id="r")
    before = (first.output_dir / runner.MANIFEST).read_bytes()
    with pytest.raises(SimulationError, match="could not be reused"):
        runner.build(protein, ligand, run_id="r", reuse=True, **kwargs)
    assert len(calls) == 1
    assert (first.output_dir / runner.MANIFEST).read_bytes() == before


def test_reuse_refuses_changed_gaussian_geometry_optimization(simulation):
    runner, protein, ligand, calls, _ = simulation
    runner.build(protein, ligand, run_id="r", gaussian_method="hf")
    with pytest.raises(SimulationError, match="could not be reused"):
        runner.build(protein, ligand, run_id="r", gaussian_method="hf", do_optimization=True, reuse=True)
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"seed": 99}, {"gmx_version": "new"}, {"interpreter": Path("other-python")},
    {"gmx": Path("other-gmx")}, {"shim_dir": Path("new-shim")},
])
def test_reuse_refuses_changed_execution_environment(simulation, changes):
    runner, protein, ligand, calls, _ = simulation
    runner.build(protein, ligand, run_id="r")
    runner.environment = replace(runner.environment, **changes)
    with pytest.raises(SimulationError, match="could not be reused"):
        runner.build(protein, ligand, run_id="r", reuse=True)
    assert len(calls) == 1


def test_reuse_refuses_changed_infrastructure_identity(simulation):
    runner, protein, ligand, calls, provenance = simulation
    runner.build(protein, ligand, run_id="r")
    provenance["source_commit"] = "changed"
    with pytest.raises(SimulationError, match="could not be reused"):
        runner.build(protein, ligand, run_id="r", reuse=True)
    assert len(calls) == 1


@pytest.mark.parametrize("missing", ["topol.top", "solv_ions.gro", "localrun.sh"])
def test_reuse_requires_all_build_products(simulation, missing):
    runner, protein, ligand, calls, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    (built.md_dir / missing).unlink()
    with pytest.raises(SimulationError, match="could not be reused"):
        runner.build(protein, ligand, run_id="r", reuse=True)
    assert len(calls) == 1


@pytest.mark.parametrize("text", ["{", "[]", "null"])
def test_corrupt_manifest_fails_closed_before_new_work(simulation, text):
    runner, protein, ligand, calls, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    (built.output_dir / runner.MANIFEST).write_text(text)
    with pytest.raises(SimulationError, match="could not be reused"):
        runner.build(protein, ligand, run_id="r", reuse=True)
    assert len(calls) == 1


def test_mid_build_input_change_cannot_attest_a_success(simulation, monkeypatch):
    runner, protein, ligand, _, _ = simulation
    original = module._run_owned

    def changing_run(*args, **kwargs):
        result = original(*args, **kwargs)
        ligand.write_text("changed during simulated execution")
        return result

    monkeypatch.setattr(module, "_run_owned", changing_run)
    built = runner.build(protein, ligand, run_id="r")
    assert not built.built and "changed" in built.detail
    assert runner.manifest_of(built.output_dir)["build"]["built"] is False


@pytest.mark.parametrize("stages", [(), ("em", "em"), ("unknown",)])
def test_invalid_drive_requirements_never_start_a_subprocess(simulation, stages):
    runner, protein, ligand, calls, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    with pytest.raises(SimulationError):
        runner.drive(built, stages=stages)
    assert len(calls) == 1


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_invalid_timeouts_fail_before_compute(simulation, timeout):
    runner, protein, ligand, calls, _ = simulation
    with pytest.raises(SimulationError, match="timeout"):
        runner.build(protein, ligand, run_id="r", timeout=timeout)
    assert not calls


def test_drive_cannot_change_environment_after_a_build(simulation):
    runner, protein, ligand, calls, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    runner.environment = replace(runner.environment, seed=123)
    with pytest.raises(SimulationError, match="manifest"):
        runner.drive(built)
    assert len(calls) == 1


def test_drive_cannot_execute_a_forged_external_build_path(simulation, tmp_path):
    runner, protein, ligand, calls, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    with pytest.raises(SimulationError, match="paths"):
        runner.drive(replace(built, md_dir=tmp_path))
    assert len(calls) == 1


def test_failed_manifest_replacement_keeps_previous_evidence(simulation, monkeypatch):
    runner, protein, ligand, _, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    path = built.output_dir / runner.MANIFEST
    before = path.read_bytes()

    def failed_replace(*_):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "replace", failed_replace)
    with pytest.raises(OSError, match="simulated disk failure"):
        runner.drive(built)
    assert path.read_bytes() == before
    assert list(built.output_dir.glob(".etalon-manifest-*")) == []


def test_empty_or_missing_stage_records_never_claim_success():
    base = DriveRecord("r", 0, (), Warnings(0), Path("unused"), ())
    assert not base.succeeded
    assert not replace(base, requested=("em",)).succeeded
    assert replace(base, requested=("em",), stages=(StageStatus("em", True, True),)).succeeded


@pytest.mark.parametrize("text", [
    "DELTA TOTAL = -1 +/- 0.1\nDELTA TOTAL = -2 +/- 0.2\n",
    "EEL = -1 +/- 0.1\nEEL = -2 +/- 0.2\nDELTA TOTAL = -2 +/- 0.2\n",
    "DELTA TOTAL = -1 +/- -0.2\n",
    "DELTA TOTAL = -1e999 +/- 0.2\n",
    "DELTA TOTAL = --1 +/- 0.2\n",
    "DELTA TOTAL = -1 +/- 0.2\nDELTA TOTAL = nan +/- nan\n",
])
def test_ambiguous_or_invalid_binding_energy_is_not_a_label(tmp_path, text):
    (tmp_path / "FINAL_RESULTS_MMPBSA.dat").write_text(text)
    assert read_binding_energy(tmp_path) is None


def test_energy_scientific_notation_is_not_silently_truncated(tmp_path):
    (tmp_path / "FINAL_RESULTS_MMPBSA.dat").write_text("DELTA TOTAL = -1.2e1 +/- 2.3e1\n")
    energy = read_binding_energy(tmp_path)
    assert energy.total_kcal_mol == -12 and energy.spread_kcal_mol == 23


@pytest.mark.parametrize("options", [
    {"stages": ()}, {"stages": ("unknown",)}, {"stages": ("em", "em")},
    {"timeout_per_molecule": 0}, {"timeout_per_molecule": float("nan")},
    {"mmpbsa_subdir": "../outside"}, {"mmpbsa_subdir": "/outside"},
    {"mmpbsa_subdir": "a\\..\\outside"},
])
def test_prism_stage_configuration_is_checked_before_spending(tmp_path, options):
    with pytest.raises(ValueError):
        PrismStage(SimpleNamespace(workspace=tmp_path), tmp_path / "protein", **options)


def test_duplicate_rows_are_rejected_before_authorization_or_materialization(tmp_path, monkeypatch):
    from etalon.authority.grant import NotAuthorized

    monkeypatch.setattr("etalon.authority.grant.require", lambda *a, **k: pytest.fail("duplicate dispatch"))
    stage = PrismStage(SimpleNamespace(workspace=tmp_path), tmp_path / "protein")
    with pytest.raises(NotAuthorized, match="unique"):
        stage([{"parent_id": "same"}, {"parent_id": "same"}], grants={})
    assert not (tmp_path / "ligands").exists()


def test_parent_ids_with_common_prefix_do_not_reuse_one_run_directory(tmp_path):
    calls = []

    def build(*args, **kwargs):
        calls.append(kwargs["run_id"])
        raise SimulationError("simulated refusal before compute")

    stage = PrismStage(SimpleNamespace(workspace=tmp_path, build=build), tmp_path / "protein")
    ids = ["parent:" + "a" * 16 + ending for ending in ("1", "2")]
    for identifier in ids:
        stage._one(identifier, tmp_path / "ligand", None)
    assert calls == [hashlib.sha256(identifier.encode()).hexdigest() for identifier in ids]
    assert len(set(calls)) == 2


def test_campaign_duplicate_handoff_cannot_spend(tmp_path):
    from etalon.campaign.ledger import Ledger
    from etalon.campaign.loop import Campaign
    from etalon.judgment.waiver import WaiverSet

    campaign = Campaign.__new__(Campaign)
    campaign.ledger = Ledger(tmp_path / "ledger.jsonl")
    campaign.waivers = WaiverSet()
    campaign.screen = SimpleNamespace(
        durability=lambda: {"durable": True}, plan=lambda *a, **k: "plan",
        run=lambda *a, **k: "result", handoff=lambda _: [{"parent_id": "same"}] * 2,
    )
    with pytest.raises(ValueError, match="unique"):
        campaign.round("r", "unused", "unused", measure=lambda *a: pytest.fail("duplicate paid work"))
    assert list(campaign.ledger.entries()) == []


def test_materialization_never_interprets_an_opaque_id_as_a_path(tmp_path):
    from rdkit import Chem

    mol = Chem.AddHs(Chem.MolFromSmiles("C"))
    row = {"parent_id": "../../outside/path", "status": "OK", "molblock": Chem.MolToMolBlock(mol),
           "heavy_atom_count": 1, "hydrogen_count": 4, "formal_charge": 0}
    result = module.materialize_ligands([row], tmp_path / "ligands")
    assert result[0].usable and result[0].path.parent == tmp_path / "ligands"
    before = result[0].path.read_bytes()
    assert module.materialize_ligands([row], tmp_path / "ligands")[0].usable
    assert result[0].path.read_bytes() == before
    # Existing incompatible geometry must never be overwritten in place.
    result[0].path.write_text("different historical geometry")
    refused = module.materialize_ligands([row], tmp_path / "ligands")
    assert not refused[0].usable and "identity" in refused[0].refused
    assert result[0].path.read_text() == "different historical geometry"


def test_materialization_does_not_follow_a_preexisting_symlink(tmp_path):
    from rdkit import Chem

    row = {"parent_id": "id", "status": "OK", "molblock": Chem.MolToMolBlock(Chem.MolFromSmiles("C"))}
    victim = tmp_path / "victim"
    victim.write_text("keep")
    path = tmp_path / f"00000_{hashlib.sha256(b'id').hexdigest()}.sdf"
    path.symlink_to(victim)
    assert not module.materialize_ligands([row], tmp_path)[0].usable
    assert victim.read_text() == "keep"


@pytest.mark.parametrize("product", ["topol.top", "solv_ions.gro", "localrun.sh"])
def test_changed_build_product_cannot_be_reused_or_driven(simulation, product):
    runner, protein, ligand, calls, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    (built.md_dir / product).write_text("different scientific input or executable")
    with pytest.raises(SimulationError, match="could not be reused"):
        runner.build(protein, ligand, run_id="r", reuse=True)
    with pytest.raises(SimulationError, match="manifest"):
        runner.drive(built)
    assert len(calls) == 1


def test_even_equal_bytes_from_external_product_symlink_are_not_a_build(simulation, tmp_path):
    runner, protein, ligand, calls, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    product = built.md_dir / "localrun.sh"
    external = tmp_path / "external-script"
    external.write_bytes(product.read_bytes())
    product.unlink()
    product.symlink_to(external)
    with pytest.raises(SimulationError, match="manifest"):
        runner.drive(built)
    assert len(calls) == 1


def test_authorization_is_rechecked_before_each_individual_spend(tmp_path, monkeypatch):
    from etalon.authority.grant import NotAuthorized
    from etalon.boundary.simulate import Materialized
    from etalon.learn.admissible import Measurement

    checks = []
    spent = []

    def require(row, *args, **kwargs):
        checks.append(row["parent_id"])
        if len(checks) == 4:
            raise NotAuthorized("simulated expiry after the first long-running molecule")

    def one(self, identifier, *args):
        spent.append(identifier)
        return Measurement(identifier, None, None)

    monkeypatch.setattr("etalon.authority.grant.require", require)
    monkeypatch.setattr("etalon.campaign.expensive.materialize_ligands", lambda rows, directory: [
        Materialized(row["parent_id"], tmp_path / "unused") for row in rows
    ])
    monkeypatch.setattr(PrismStage, "_one", one)
    stage = PrismStage(SimpleNamespace(workspace=tmp_path), tmp_path / "protein")
    with pytest.raises(NotAuthorized, match="expiry"):
        stage([{"parent_id": "first"}, {"parent_id": "second"}], grants={})
    assert spent == ["first"] and checks == ["first", "second", "first", "second"]


@pytest.mark.parametrize("symlink_directory", [False, True])
def test_stage_cannot_import_energy_from_outside_its_build(simulation, tmp_path, monkeypatch, symlink_directory):
    runner, protein, ligand, _, _ = simulation
    run_id = hashlib.sha256(b"molecule").hexdigest()
    built = runner.build(protein, ligand, run_id=run_id)
    outside = tmp_path / "another-run"
    outside.mkdir()
    (outside / "FINAL_RESULTS_MMPBSA.dat").write_text("DELTA TOTAL = -10 +/- 1\n")
    local = built.output_dir / "GMX_PROLIG_MMPBSA"
    if symlink_directory:
        local.symlink_to(outside, target_is_directory=True)
    else:
        local.mkdir()
        (local / "FINAL_RESULTS_MMPBSA.dat").symlink_to(outside / "FINAL_RESULTS_MMPBSA.dat")
    drive = DriveRecord(run_id, 0, tuple(StageStatus(name, True, True) for name in ("em", "nvt", "npt")),
                        Warnings(0), tmp_path / "unused", ("em", "nvt", "npt"))
    monkeypatch.setattr(runner, "drive", lambda *a, **k: drive)
    measured = PrismStage(runner, protein)._one("molecule", ligand, -1)
    assert measured.expensive_value is None and "binding_energy" not in measured.provenance


def _not_running(pid):
    """Linux may briefly retain killed grandchildren as init-owned zombies, not work."""
    for _ in range(100):
        status = Path(f"/proc/{pid}/stat")
        if not status.exists():
            return True
        try:
            if status.read_text().split(")", 1)[1].strip().startswith("Z"):
                return True
        except (FileNotFoundError, ProcessLookupError):
            return True
        time.sleep(0.01)
    return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="owned-process liveness assertion uses /proc")
def test_timeout_kills_only_its_owned_parent_and_child_group():
    neighbour = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], start_new_session=True)
    program = (
        "import subprocess,sys,time,os,json\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])\n"
        "print(json.dumps({'parent':os.getpid(),'child':child.pid}),flush=True)\n"
        "time.sleep(10)\n"
    )
    try:
        captured = module._run_owned([sys.executable, "-c", program], timeout=0.3, env=os.environ)
        ids = json.loads(captured.stdout.strip())
        assert captured.status == "timed_out"
        assert captured.process_group == ids["parent"] != os.getpgrp()
        assert _not_running(ids["parent"]) and _not_running(ids["child"])
        assert neighbour.poll() is None
        assert captured.as_dict()["cost"] is None and captured.as_dict()["cost_status"] == "unknown"
        assert captured.cleanup["group_kill_sent"] and captured.cleanup["leader_reaped"]
    finally:
        neighbour.kill()
        neighbour.wait(timeout=2)


def test_owned_process_keeps_partial_stdout_and_stderr_exactly_once():
    if not module._PROCESS_GROUPS_SUPPORTED:
        pytest.skip("POSIX-only contract")
    program = "import sys,time;print('partial-out',flush=True);print('partial-err',file=sys.stderr,flush=True);time.sleep(10)"
    result = module._run_owned([sys.executable, "-c", program], timeout=0.2, env=os.environ)
    assert result.status == "timed_out"
    assert result.stdout.count("partial-out") == result.stderr.count("partial-err") == 1
    assert result.elapsed_seconds < 4


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="owned-process liveness assertion uses /proc")
def test_interruption_cleans_owned_group_and_retains_capture(monkeypatch):
    original = subprocess.Popen.communicate
    first = True

    def interrupted(self, *args, **kwargs):
        nonlocal first
        if first:
            first = False
            try:
                original(self, timeout=0.2)
            except subprocess.TimeoutExpired:
                raise KeyboardInterrupt from None
        return original(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    program = "import os,time;print(os.getpid(),flush=True);time.sleep(10)"
    result = module._run_owned([sys.executable, "-c", program], timeout=5, env=os.environ)
    assert result.status == "interrupted" and _not_running(int(result.stdout.strip()))
    assert result.as_dict()["recovery_required"] is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="owned-process liveness assertion uses /proc")
def test_zero_exit_cannot_leave_a_background_compute_child_untracked():
    program = (
        "import subprocess,sys\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "print(child.pid,flush=True)\n"
    )
    result = module._run_owned([sys.executable, "-c", program], timeout=2, env=os.environ)
    assert result.returncode == 0 and result.status == "orphaned_children"
    assert _not_running(int(result.stdout.strip()))
    assert result.as_dict()["recovery_required"] is True


def test_non_posix_execution_refuses_before_process_creation(monkeypatch):
    monkeypatch.setattr(module, "_PROCESS_GROUPS_SUPPORTED", False)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("unsupported dispatch"))
    with pytest.raises(SimulationError, match="POSIX"):
        module._run_owned(["unused"], timeout=1, env={})


@pytest.mark.parametrize("status,returncode", [("timed_out", -9), ("failed", 1), ("orphaned_children", 0)])
def test_build_failure_is_paid_unknown_even_when_all_products_exist(simulation, monkeypatch, status, returncode):
    runner, protein, ligand, _, _ = simulation
    original = module._run_owned

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        return module._CapturedRun(returncode, "partial build output", "error", status)

    monkeypatch.setattr(module, "_run_owned", failed)
    built = runner.build(protein, ligand, run_id="failed")
    assert not built.built and len(built.products_sha256) == 3
    assert built.execution["status"] == status
    assert built.execution["cost_status"] == "unknown" and built.execution["cost"] is None
    assert "partial build output" in built.stdout_path.read_text()
    assert runner.manifest_of(built.output_dir)["build"]["built"] is False
    with pytest.raises(SimulationError, match="operator review"):
        runner.build(protein, ligand, run_id="failed", overwrite=True)


def test_build_interruption_is_recorded_then_reraised(simulation, monkeypatch):
    runner, protein, ligand, _, _ = simulation
    monkeypatch.setattr(module, "_run_owned", lambda *a, **k: module._CapturedRun(-9, "partial", "", "interrupted"))
    with pytest.raises(KeyboardInterrupt, match="recorded"):
        runner.build(protein, ligand, run_id="interrupted")
    recorded = runner.manifest_of(runner.workspace / "interrupted")["build"]
    assert not recorded["built"] and recorded["execution"]["status"] == "interrupted"
    assert Path(recorded["captured_output"]).read_text().count("partial") == 1


def test_build_writes_pending_intent_before_any_subprocess(simulation, monkeypatch):
    runner, protein, ligand, _, _ = simulation

    def check_pending(*args, **kwargs):
        pending = runner.manifest_of(runner.workspace / "pending")["build"]
        assert pending["execution"]["status"] == "running" and not pending["built"]
        raise SystemExit("simulated uncatchable controller termination")

    monkeypatch.setattr(module, "_run_owned", check_pending)
    with pytest.raises(SystemExit):
        runner.build(protein, ligand, run_id="pending")
    with pytest.raises(SimulationError, match="operator review"):
        runner.build(protein, ligand, run_id="pending", overwrite=True)


@pytest.mark.parametrize("status,returncode", [("timed_out", -9), ("failed", 1), ("orphaned_children", 0)])
def test_failed_drive_keeps_finished_artifacts_without_claiming_success(simulation, monkeypatch, status, returncode):
    runner, protein, ligand, _, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    for _name, _tpr, product in module.STAGE_PRODUCTS:
        (built.md_dir / product).parent.mkdir()
        (built.md_dir / product).write_text("actual fixture product")
    monkeypatch.setattr(module, "_run_owned", lambda *a, **k: module._CapturedRun(returncode, "partial", "", status))
    driven = runner.drive(built)
    assert not driven.succeeded and driven.finished == ("em", "nvt", "npt", "prod")
    assert driven.execution["cost_status"] == "unknown"
    with pytest.raises(SimulationError, match="operator review"):
        runner.drive(built)


def test_manual_drive_recovery_retains_both_attempts_and_original_output(simulation, monkeypatch):
    runner, protein, ligand, _, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    monkeypatch.setattr(module, "_run_owned", lambda *a, **k: module._CapturedRun(-9, "first partial", "", "timed_out"))
    first = runner.drive(built)
    monkeypatch.setattr(module, "_run_owned", lambda *a, **k: module._CapturedRun(0, "second complete", ""))
    reason = "operator verified previous compute is stopped"
    second = runner.drive(built, confirm_recovery=True, recovery_reason=reason)
    assert first.stdout_path != second.stdout_path
    assert "first partial" in first.stdout_path.read_text()
    assert "second complete" in second.stdout_path.read_text()
    attempts = runner.manifest_of(built.output_dir)["drive_attempts"]
    assert len(attempts) == 2 and attempts[0]["timed_out"] is True
    assert attempts[1]["execution"]["recovery_reason"] == reason


def test_drive_interruption_keeps_pending_then_final_record_and_reraises(simulation, monkeypatch):
    runner, protein, ligand, _, _ = simulation
    built = runner.build(protein, ligand, run_id="r")

    def interrupt(*args, **kwargs):
        assert runner.manifest_of(built.output_dir)["drive"]["execution"]["status"] == "running"
        return module._CapturedRun(-9, "partial interrupted drive", "", "interrupted")

    monkeypatch.setattr(module, "_run_owned", interrupt)
    with pytest.raises(KeyboardInterrupt, match="recorded"):
        runner.drive(built)
    recorded = runner.manifest_of(built.output_dir)["drive"]
    assert not recorded["succeeded"] and recorded["execution"]["status"] == "interrupted"
    with pytest.raises(SimulationError, match="operator review"):
        runner.drive(built)


@pytest.mark.parametrize("options", [
    {"confirm_recovery": "yes"}, {"confirm_recovery": True},
    {"confirm_recovery": True, "recovery_reason": "ok"},
    {"recovery_reason": "operator checked all processes"},
])
def test_recovery_confirmation_requires_explicit_valid_operator_reason(simulation, options):
    runner, protein, ligand, calls, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    with pytest.raises(SimulationError, match="recovery"):
        runner.drive(built, **options)
    assert len(calls) == 1


@pytest.mark.parametrize("returncode,timed_out,status", [(1, False, "failed"), (-9, True, "timed_out"), (-9, False, "interrupted")])
def test_prism_stage_withholds_residual_energy_after_unclean_termination(simulation, monkeypatch, returncode, timed_out, status):
    runner, protein, ligand, _, _ = simulation
    run_id = hashlib.sha256(b"molecule").hexdigest()
    built = runner.build(protein, ligand, run_id=run_id)
    output = built.output_dir / "GMX_PROLIG_MMPBSA"
    output.mkdir()
    (output / "FINAL_RESULTS_MMPBSA.dat").write_text("DELTA TOTAL = -10 +/- 1\n")
    drive = DriveRecord(run_id, returncode, tuple(StageStatus(name, True, True) for name in ("em", "nvt", "npt")),
                        Warnings(0), built.output_dir / "simulated.log", ("em", "nvt", "npt"), timed_out,
                        execution={"status": status, "cost": None, "cost_status": "unknown"})
    monkeypatch.setattr(runner, "drive", lambda *a, **k: drive)
    measured = PrismStage(runner, protein)._one("molecule", ligand, -1)
    assert measured.expensive_value is None
    assert measured.provenance["binding_energy"]["delta_g_bind_kcal_mol"] == -10
    assert "binding_energy_withheld_reason" in measured.provenance
    assert measured.provenance["execution"]["cost_status"] == "unknown"


@pytest.mark.parametrize("second_operation", ["drive", "overwrite"])
def test_live_drive_exclusively_locks_same_run_including_confirmed_overwrite(simulation, monkeypatch, second_operation):
    runner, protein, ligand, _, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    entered, release = Event(), Event()
    calls = []

    def running(*args, **kwargs):
        calls.append(args)
        entered.set()
        assert release.wait(timeout=2)
        return module._CapturedRun(0, "owned execution", "")

    monkeypatch.setattr(module, "_run_owned", running)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(runner.drive, built)
        assert entered.wait(timeout=2)
        try:
            with pytest.raises(SimulationError, match="execution lock"):
                if second_operation == "drive":
                    runner.drive(built, confirm_recovery=True, recovery_reason="operator cannot override a live owner")
                else:
                    runner.build(protein, ligand, run_id="r", overwrite=True, confirm_recovery=True,
                                 recovery_reason="operator cannot override a live owner")
        finally:
            release.set()
        first.result(timeout=2)
    assert len(calls) == 1 and (built.md_dir / "topol.top").is_file()


def test_different_run_ids_have_independent_execution_locks(simulation, monkeypatch):
    runner, protein, ligand, _, _ = simulation
    original = module._run_owned
    entered, release = Event(), Event()

    def running(command, **kwargs):
        if Path(command[5]).name == "first":
            entered.set()
            assert release.wait(timeout=2)
        return original(command, **kwargs)

    monkeypatch.setattr(module, "_run_owned", running)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(runner.build, protein, ligand, run_id="first")
        assert entered.wait(timeout=2)
        try:
            assert runner.build(protein, ligand, run_id="second").built
        finally:
            release.set()
        assert first.result(timeout=2).built


def test_separate_controller_process_cannot_be_bypassed_by_recovery_confirmation(simulation):
    runner, protein, ligand, calls, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    lock_path = runner.workspace / ".etalon_execution_locks" / (hashlib.sha256(b"r").hexdigest() + ".lock")
    program = (
        "import fcntl,sys,time\n"
        "lock=open(sys.argv[1],'a')\n"
        "fcntl.flock(lock,fcntl.LOCK_EX)\n"
        "print('owned-lock',flush=True)\n"
        "time.sleep(10)\n"
    )
    controller = subprocess.Popen([sys.executable, "-c", program, str(lock_path)], stdout=subprocess.PIPE,
                                  text=True, start_new_session=True)
    try:
        assert controller.stdout.readline().strip() == "owned-lock"
        with pytest.raises(SimulationError, match="execution lock"):
            runner.drive(built, confirm_recovery=True, recovery_reason="cannot bypass a separate live controller")
        assert len(calls) == 1
    finally:
        controller.kill()
        controller.wait(timeout=2)
        controller.stdout.close()
    # flock is released by process exit; the same inode is retained for all controllers.
    assert lock_path.is_file()
    runner.drive(built)
    assert len(calls) == 2


def test_internal_lock_directory_cannot_itself_be_overwritten_as_a_run(simulation):
    runner, protein, ligand, calls, _ = simulation
    with pytest.raises(SimulationError, match="run_id"):
        runner.build(protein, ligand, run_id=".etalon_execution_locks", overwrite=True)
    assert calls == []


def test_execution_lock_symlink_is_not_followed(simulation, tmp_path):
    runner, protein, ligand, calls, _ = simulation
    directory = runner.workspace / ".etalon_execution_locks"
    directory.mkdir()
    victim = tmp_path / "unrelated"
    victim.write_text("preserved")
    (directory / (hashlib.sha256(b"r").hexdigest() + ".lock")).symlink_to(victim)
    with pytest.raises(SimulationError, match="safely open"):
        runner.build(protein, ligand, run_id="r")
    assert not calls and victim.read_text() == "preserved"


def test_rebuilding_cannot_erase_an_unreviewed_interrupted_drive(simulation, monkeypatch):
    runner, protein, ligand, _, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    monkeypatch.setattr(module, "_run_owned", lambda *a, **k: module._CapturedRun(-9, "partial", "", "interrupted"))
    with pytest.raises(KeyboardInterrupt):
        runner.drive(built)
    before = (built.output_dir / runner.MANIFEST).read_bytes()
    with pytest.raises(SimulationError, match="operator review"):
        runner.build(protein, ligand, run_id="r", overwrite=True)
    assert (built.output_dir / runner.MANIFEST).read_bytes() == before


def test_execution_lock_refuses_missing_nofollow_instead_of_weakening_safety(simulation, monkeypatch):
    runner, protein, ligand, calls, _ = simulation
    monkeypatch.delattr(module.os, "O_NOFOLLOW")
    with pytest.raises(SimulationError, match="O_NOFOLLOW"):
        runner.build(protein, ligand, run_id="r")
    assert not calls and not (runner.workspace / ".etalon_execution_locks").exists()


def test_fifo_cannot_be_an_execution_lock_and_open_is_nonblocking(simulation, monkeypatch):
    runner, protein, ligand, calls, _ = simulation
    directory = runner.workspace / ".etalon_execution_locks"
    directory.mkdir()
    lock_path = directory / (hashlib.sha256(b"r").hexdigest() + ".lock")
    os.mkfifo(lock_path)
    original = os.open
    seen = []

    def checked_open(path, flags, *args, **kwargs):
        seen.append(flags)
        assert flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", checked_open)
    with pytest.raises(SimulationError, match="regular file"):
        runner.build(protein, ligand, run_id="r")
    assert seen and not calls


# --------------------------------------------------------------------------------------
# A product on disk is not a product this attempt made. The build path already knows the
# difference -- _reusable() digests topol.top, solv_ions.gro and localrun.sh and refuses a
# manifest whose products no longer hash the same. The drive path judged a stage purely by
# whether its .gro existed, so an attempt that ran nothing inherited the previous attempt's
# products and reported them as its own.
# --------------------------------------------------------------------------------------


def _plant_products(md_dir, stages):
    """Write what an earlier attempt would have left behind."""
    for name, tpr, product in module.STAGE_PRODUCTS:
        if name not in stages:
            continue
        (md_dir / tpr).parent.mkdir(parents=True, exist_ok=True)
        (md_dir / tpr).write_text("earlier attempt")
        (md_dir / product).write_text("earlier attempt")


def test_a_product_left_by_an_earlier_attempt_is_not_reported_as_this_attempt_s(simulation):
    runner, protein, ligand, _, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    _plant_products(built.md_dir, ("em", "nvt"))

    # The fixture's fake driver writes nothing, so every product below predates this attempt.
    drive = runner.drive(built, stages=("em", "nvt"))

    assert drive.finished == ("em", "nvt"), "the products exist and that stays a fact"
    assert drive.produced == (), "and this attempt wrote none of them"
    assert drive.inherited == ("em", "nvt")
    assert [s.state for s in drive.stages if s.stage in ("em", "nvt")] == [
        "FINISHED_EARLIER", "FINISHED_EARLIER",
    ]
    assert drive.as_dict()["inherited"] == ["em", "nvt"]
    assert drive.failed == (), "an inherited product is not a failed stage"


def test_a_product_this_attempt_rewrote_is_reported_as_its_own(simulation, monkeypatch):
    runner, protein, ligand, _, _ = simulation
    built = runner.build(protein, ligand, run_id="r")
    _plant_products(built.md_dir, ("em",))

    def run(command, **kwargs):
        if command[0] == "bash":
            for name, tpr, product in module.STAGE_PRODUCTS:
                if name == "em":
                    (built.md_dir / product).write_text("this attempt, different bytes")
        return module._CapturedRun(0, "simulated output", "")

    monkeypatch.setattr(module, "_run_owned", run)
    drive = runner.drive(built, stages=("em",))

    assert drive.produced == ("em",)
    assert drive.inherited == ()
    assert drive.succeeded


# --------------------------------------------------------------------------------------
# FINAL_RESULTS_MMPBSA.dat is written by gmx_MMPBSA, which nothing in ETALON runs: the
# operator runs mmpbsa_run.sh. So the number is always older than the drive that reads it,
# and "did this drive produce it" is the wrong question. The answerable one is whether it
# predates the system it is now attributed to -- a rebuild replaces topol.top and leaves the
# old .dat sitting in GMX_PROLIG_MMPBSA, where it was read as this build's affinity.
# --------------------------------------------------------------------------------------


def _driving(simulation, monkeypatch, stages=("em",)):
    """A fixture driver that actually writes the requested stage products."""
    runner, protein, ligand, _, _ = simulation

    def run(command, **kwargs):
        if command[0] == "bash":
            md = Path(kwargs["cwd"])
            for name, tpr, product in module.STAGE_PRODUCTS:
                if name in stages:
                    (md / tpr).parent.mkdir(parents=True, exist_ok=True)
                    (md / tpr).write_text("driven")
                    (md / product).write_text("driven")
        else:
            md = Path(command[5]) / "GMX_PROLIG_MD"
            md.mkdir(parents=True, exist_ok=True)
            for name in ("topol.top", "solv_ions.gro", "localrun.sh"):
                (md / name).write_text("simulation fixture only")
        return module._CapturedRun(0, "simulated output", "")

    monkeypatch.setattr(module, "_run_owned", run)
    return PrismStage(runner, protein, stages=stages, timeout_per_molecule=60)


def _plant_energy(stage, identifier, *, older_than_build):
    run_dir = stage.simulate.workspace.resolve() / hashlib.sha256(identifier.encode()).hexdigest()
    energy_dir = run_dir / stage.mmpbsa_subdir
    energy_dir.mkdir(parents=True, exist_ok=True)
    path = energy_dir / "FINAL_RESULTS_MMPBSA.dat"
    path.write_text("DELTA TOTAL = -42.0 +/- 1.0\n")
    reference = (run_dir / "GMX_PROLIG_MD" / "topol.top").stat().st_mtime_ns
    offset = -60_000_000_000 if older_than_build else 60_000_000_000
    os.utime(path, ns=(reference + offset, reference + offset))
    return path


def test_an_energy_older_than_the_system_it_describes_is_not_an_admitted_label(
    simulation, monkeypatch, tmp_path
):
    stage = _driving(simulation, monkeypatch)
    ligand = tmp_path / "lig.sdf"
    ligand.write_text("fixture ligand")

    first = stage._one("M1", ligand, -8.2)
    assert first.provenance["no_binding_energy"], "nothing has computed an affinity yet"

    _plant_energy(stage, "M1", older_than_build=True)
    stale = stage._one("M1", ligand, -8.2)

    assert stale.provenance["binding_energy"]["delta_g_bind_kcal_mol"] == -42.0, "the number is retained"
    assert stale.expensive_value is None, "and it is not fitted against every later molecule"
    assert "older than" in stale.provenance["binding_energy_withheld_reason"]


def test_an_energy_computed_after_the_build_is_admitted(simulation, monkeypatch, tmp_path):
    stage = _driving(simulation, monkeypatch)
    ligand = tmp_path / "lig.sdf"
    ligand.write_text("fixture ligand")
    stage._one("M1", ligand, -8.2)

    _plant_energy(stage, "M1", older_than_build=False)
    fresh = stage._one("M1", ligand, -8.2)

    assert fresh.expensive_value == -42.0
    assert "binding_energy_withheld_reason" not in fresh.provenance


def test_the_withheld_reason_is_a_code_the_admissibility_layer_can_read(
    simulation, monkeypatch, tmp_path
):
    """A reason in provenance is prose. The layer that decides reads codes.

    ``etalon.learn.admissible.rule`` raises ``KeyError`` on an observation naming a fault
    outside the taxonomy, so emitting the finding as an observation is not optional
    decoration -- either the code is registered and the withholding is machine-readable and
    waivable, or the stage that reports it crashes the loop that consumes it.
    """

    from etalon.learn.admissible import Admission, rule

    stage = _driving(simulation, monkeypatch)
    ligand = tmp_path / "lig.sdf"
    ligand.write_text("fixture ligand")
    stage._one("M1", ligand, -8.2)
    _plant_energy(stage, "M1", older_than_build=True)

    verdict = rule(stage._one("M1", ligand, -8.2))

    assert verdict.admission is Admission.WITHHELD
    assert "F_RESULT_PREDATES_THE_SYSTEM" in verdict.blocking
