"""The expensive stage, and the three ways it lies about having worked.

PRISM builds correct systems and keeps no record of having built them. So almost every
test here is about a claim that is easy to make and wrong: that the driver's exit code
means something, that a build's return means a topology exists, that a file written from a
record still matches the record.

None of these need a GPU or AmberTools. That is deliberate -- the failure detection is the
part most likely to be wrong and the part hardest to exercise on real hardware, so it is
written to be judged from files on disk rather than from a running simulation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.boundary.simulate import (
    REQUIRED_TOOLS,
    STAGE_PRODUCTS,
    DriveRecord,
    Environment,
    SimulationError,
    StageStatus,
    Warnings,
    discover,
    materialize_ligands,
    read_warnings,
)

#: A real RDKit molblock: ethanol with explicit hydrogens, 3 heavy atoms and 6 hydrogens.
#: The first line is the title and is blank, which is what RDKit writes. Leaving it out
#: shifts every later line by one and the counts line lands where the comment belongs --
#: which the materializer refuses, correctly, and which cost a confused minute when this
#: fixture was written without it.
ETHANOL = """
     RDKit          3D

  9  8  0  0  0  0  0  0  0  0999 V2000
    1.2154   -0.2169    0.0308 C   0  0  0  0  0  0  0  0  0  0  0  0
   -0.0010    0.5443   -0.0674 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.1345   -0.2911    0.1163 O   0  0  0  0  0  0  0  0  0  0  0  0
    2.1110    0.4011   -0.0742 H   0  0  0  0  0  0  0  0  0  0  0  0
    1.2389   -0.7190    1.0055 H   0  0  0  0  0  0  0  0  0  0  0  0
    1.2577   -0.9846   -0.7502 H   0  0  0  0  0  0  0  0  0  0  0  0
   -0.0495    1.0867   -1.0206 H   0  0  0  0  0  0  0  0  0  0  0  0
   -0.0366    1.2948    0.7345 H   0  0  0  0  0  0  0  0  0  0  0  0
   -1.9516    0.2180    0.1649 H   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  1  4  1  0
  1  5  1  0
  1  6  1  0
  2  3  1  0
  2  7  1  0
  2  8  1  0
  3  9  1  0
M  END
"""


def _row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "parent_id": "parent:sha256:abc123def456",
        "molblock": ETHANOL,
        "heavy_atom_count": 3,
        "hydrogen_count": 6,
        "formal_charge": 0,
        "status": "OK",
    }
    base.update(over)
    return base


# -- the environment -------------------------------------------------------


def test_a_missing_tool_is_named_rather_than_discovered_an_hour_in() -> None:
    """In this installation the screen's environment genuinely lacks AmberTools.

    Which is the point: the probe's job is to say so before a build starts, because
    "PRISM failed" after a parameterisation is a much worse message than "antechamber is
    not on the path of that interpreter".
    """

    probe = discover(search=("/nonexistent-for-this-test",))

    assert set(probe.missing) <= set(REQUIRED_TOOLS)
    if probe.missing:
        assert not probe.ready


def test_a_seed_alone_does_not_make_a_build_reproducible() -> None:
    """It needs the shim too, and conflating the two would overstate the record."""

    seeded = Environment(interpreter=Path(sys.executable), gmx=Path("/usr/bin/true"), seed=7)
    both = Environment(
        interpreter=Path(sys.executable),
        gmx=Path("/usr/bin/true"),
        seed=7,
        shim_dir=Path("/tmp/shim"),
    )

    assert seeded.reproducible is False
    assert both.reproducible is True


def test_the_shim_goes_ahead_of_the_real_gmx_on_the_child_path() -> None:
    """That ordering is the entire mechanism; reversed, the shim never runs."""

    environment = Environment(
        interpreter=Path("/opt/env/bin/python"),
        gmx=Path("/usr/local/gromacs/bin/gmx"),
        shim_dir=Path("/tmp/shim"),
        seed=11,
    )

    exported = environment.exported(asset_root=Path("/assets/prism"))
    parts = exported["PATH"].split(":")

    assert parts[0] == "/tmp/shim"
    assert parts.index("/tmp/shim") < parts.index("/usr/local/gromacs/bin")
    assert exported["ETALON_GENION_SEED"] == "11"
    # The pinned tree, or the manifest would cite a commit for work done by something else.
    assert exported["PYTHONPATH"].split(":")[0] == "/assets/prism"


def test_an_unready_environment_refuses_to_build(tmp_path: Path) -> None:
    from etalon.boundary.simulate import Simulate

    unready = Environment(interpreter=Path(sys.executable), gmx=None, missing=("antechamber",))
    # infra is not loaded: the refusal must happen before anything expensive, including
    # resolving the asset.
    runner = Simulate.__new__(Simulate)
    runner.workspace = tmp_path
    runner.environment = unready

    with pytest.raises(SimulationError, match="antechamber"):
        Simulate.build(runner, tmp_path / "p.pdb", tmp_path / "l.mol2", run_id="r")


# -- failure detection -----------------------------------------------------


def test_success_is_the_product_existing_and_not_the_exit_code() -> None:
    """The measured behaviour of localrun.sh, which has no set -e.

    A failed grompp writes no tpr, mdrun fails on the missing file, the next stage runs
    grompp against a gro that was never written, and the script still exits 0. An exit
    code here is evidence of nothing.
    """

    nothing_ran = DriveRecord(
        run_id="r",
        exit_code=0,  # the script's own verdict, and wrong
        stages=tuple(StageStatus(name, tpr=False, product=False) for name, _, _ in STAGE_PRODUCTS),
        warnings=Warnings(total=0),
        stdout_path=Path("/dev/null"),
        requested=("em", "nvt", "npt"),
    )

    assert nothing_ran.exit_code == 0
    assert nothing_ran.succeeded is False
    assert [s.stage for s in nothing_ran.failed] == ["em", "nvt", "npt"]
    assert all(s.state == "NEVER_STARTED" for s in nothing_ran.failed)


def test_grompp_working_and_mdrun_not_is_a_distinct_state() -> None:
    """The interesting failure: the system was buildable and the simulation was not."""

    assert StageStatus("em", tpr=True, product=False).state == "STARTED_NOT_FINISHED"
    assert StageStatus("em", tpr=True, product=True).state == "FINISHED"
    assert StageStatus("em", tpr=False, product=False).state == "NEVER_STARTED"


def test_only_the_requested_stages_decide_success() -> None:
    """A campaign that wanted equilibration is not failed by an unrun production stage."""

    equilibrated = DriveRecord(
        run_id="r",
        exit_code=0,
        stages=(
            StageStatus("em", True, True),
            StageStatus("nvt", True, True),
            StageStatus("npt", True, True),
            StageStatus("prod", False, False),
        ),
        warnings=Warnings(total=0),
        stdout_path=Path("/dev/null"),
        requested=("em", "nvt", "npt"),
    )

    assert equilibrated.succeeded is True
    assert equilibrated.finished == ("em", "nvt", "npt")


def test_the_stage_products_are_the_scripts_own_skip_conditions() -> None:
    """If these drift from localrun.sh, this layer and PRISM disagree about "finished".

    Pinned by value so a PRISM upgrade that renames an output fails here rather than
    reporting every stage as never started.
    """

    assert STAGE_PRODUCTS == (
        ("em", "em/em.tpr", "em/em.gro"),
        ("nvt", "nvt/nvt.tpr", "nvt/nvt.gro"),
        ("npt", "npt/npt.tpr", "npt/npt.gro"),
        ("prod", "prod/md.tpr", "prod/md.gro"),
    )


def test_the_warnings_prism_tells_grompp_to_ignore_are_kept() -> None:
    """Every grompp in the driver carries -maxwarn 999, so none of these stopped a build."""

    captured = """
Generated 330891 of the 330891 non-bonded parameter combinations
WARNING 1 [file topol.top, line 42]:
  System has non-zero total charge: -0.999999
WARNING 2 [file topol.top, line 77]:
  atom name 5 in topol.top and solv_ions.gro does not match (HB1 - HB2)
Setting the LD random seed to 1993745
"""

    found = read_warnings(captured)

    assert found.total == 2
    assert found.by_kind["non_integer_charge"] == 1
    assert found.by_kind["atom_name_mismatch"] == 1
    assert "maxwarn" in str(found.as_dict()["note"])


def test_clean_output_reports_no_warnings() -> None:
    assert read_warnings("Setting the LD random seed\nsteepest descents converged\n").total == 0


# -- the seam --------------------------------------------------------------


def test_the_molblock_is_written_through_unchanged(tmp_path: Path) -> None:
    """No parse, no re-embed, no round trip -- a round trip can lose a coordinate."""

    written = materialize_ligands([_row()], tmp_path)

    assert len(written) == 1 and written[0].usable
    text = written[0].path.read_text(encoding="utf-8")
    assert "RDKit          3D" in text
    assert "M  END" in text
    assert text.rstrip().endswith("$$$$")
    # Every coordinate line from the record survives verbatim.
    for line in ETHANOL.splitlines():
        if line.strip():
            assert line in text


def test_a_file_that_disagrees_with_its_record_is_refused(tmp_path: Path) -> None:
    """Every check the campaign ran was against the record.

    So a file that does not match it has not been checked at all, and handing it to a
    force field would make the whole preflight pass a description of something else.
    """

    written = materialize_ligands([_row(hydrogen_count=21)], tmp_path)

    assert not written[0].usable
    assert "6 hydrogens" in written[0].refused
    assert "declares 3, 21" in written[0].refused


def test_a_molblock_missing_its_title_line_is_refused(tmp_path: Path) -> None:
    """A molblock is positional: line 1 title, line 2 program, line 3 comment, line 4 counts.

    Drop the blank title and the counts line lands in the comment slot. Nothing about the
    text looks wrong, and the file is no longer a MOL file. Refused rather than repaired,
    because guessing which line was dropped is guessing at the molecule.
    """

    truncated = ETHANOL.lstrip("\n")

    written = materialize_ligands([_row(molblock=truncated)], tmp_path)

    assert not written[0].usable
    assert "does not parse" in written[0].refused


def test_a_record_with_no_geometry_produces_no_file(tmp_path: Path) -> None:
    no_block = materialize_ligands([_row(molblock=None)], tmp_path)
    not_ok = materialize_ligands([_row(status="NO_GEOMETRY")], tmp_path)

    assert not no_block[0].usable and "no molblock" in no_block[0].refused
    assert not not_ok[0].usable and "NO_GEOMETRY" in not_ok[0].refused


def test_two_molecules_cannot_collide_on_one_filename(tmp_path: Path) -> None:
    """parent_id is a content digest, and a digest tail is not unique enough alone."""

    same_tail = "parent:sha256:" + "a" * 64
    written = materialize_ligands([_row(parent_id=same_tail), _row(parent_id=same_tail)], tmp_path)

    assert all(item.usable for item in written)
    assert written[0].path != written[1].path
    assert len(list(tmp_path.glob("*.sdf"))) == 2


def test_a_timed_out_run_still_reports_the_stages_that_finished() -> None:
    """PRISM's default production length is 500 ns, so this is the ordinary case.

    A campaign that asked for equilibration will hit the wall clock with em, nvt and npt
    already on disk. Raising the timeout out of ``drive`` would discard the record of work
    that really happened.
    """

    cut_short = DriveRecord(
        run_id="r",
        exit_code=None,
        stages=(
            StageStatus("em", True, True),
            StageStatus("nvt", True, True),
            StageStatus("npt", True, True),
            StageStatus("prod", True, False),
        ),
        warnings=Warnings(total=0),
        stdout_path=Path("/dev/null"),
        requested=("em", "nvt", "npt"),
        timed_out=True,
    )

    assert cut_short.exit_code is None
    assert cut_short.timed_out is True
    # Products remain evidence of completed stages, not proof of clean termination.
    assert cut_short.succeeded is False
    assert cut_short.finished == ("em", "nvt", "npt")
    assert cut_short.as_dict()["timed_out"] is True


def test_the_measured_exit_zero_case_is_pinned() -> None:
    """findings/0002: a re-driven directory exits 0 with every equilibration stage failed.

    The final block takes its ``if [ -f ./prod/md.gro ]`` skip branch, ``echo`` succeeds,
    and that is the script's exit status. Measured, with 23 error lines in the log. Pinned
    here because an adapter that ever goes back to reading the exit code would pass every
    other test in this file.
    """

    resumed = DriveRecord(
        run_id="r",
        exit_code=0,
        stages=(
            StageStatus("em", False, False),
            StageStatus("nvt", False, False),
            StageStatus("npt", False, False),
            StageStatus("prod", False, True),
        ),
        warnings=Warnings(total=0),
        stdout_path=Path("/dev/null"),
        requested=("em", "nvt", "npt"),
    )

    assert resumed.exit_code == 0
    assert resumed.succeeded is False
    assert [s.stage for s in resumed.failed] == ["em", "nvt", "npt"]
    assert resumed.finished == ("prod",)
    assert "exits 0 with em, nvt" in str(resumed.as_dict()["note"])


# -- reading results back --------------------------------------------------


def test_a_binding_energy_is_read_with_prisms_own_pattern(tmp_path: Path) -> None:
    """One file, two readers, and they must not disagree about what it says."""

    from etalon.boundary.simulate import read_binding_energy

    (tmp_path / "FINAL_RESULTS_MMPBSA.dat").write_text(
        "GENERALIZED BORN:\n\n"
        "VDWAALS     =     -42.1000 +/-    1.5000\n"
        "EEL         =     -15.3000 +/-    3.2000\n"
        "DELTA TOTAL =     -35.2000 +/-    2.1000\n",
        encoding="utf-8",
    )

    energy = read_binding_energy(tmp_path)

    assert energy is not None
    assert energy.total_kcal_mol == pytest.approx(-35.2)
    assert energy.spread_kcal_mol == pytest.approx(2.1)
    assert energy.components["VDWAALS"] == pytest.approx(-42.1)
    # The +/- figure must never be presented as an error bar on the free energy.
    assert "not an error bar" in str(energy.as_dict()["spread_is_not_an_uncertainty"])
    assert "within_run_spread_kcal_mol" in energy.as_dict()


def test_no_result_yet_is_none_rather_than_an_exception(tmp_path: Path) -> None:
    """A campaign in progress has molecules with no number, and that is ordinary."""

    from etalon.boundary.simulate import read_binding_energy

    assert read_binding_energy(tmp_path) is None
    assert read_binding_energy(tmp_path / "never-existed") is None


def test_a_build_is_reused_only_when_the_inputs_match(tmp_path: Path) -> None:
    """A directory named for a molecule is not evidence that it holds that molecule."""

    import json

    from etalon.boundary.simulate import Simulate

    runner = Simulate.__new__(Simulate)
    runner.workspace = tmp_path
    runner.environment = Environment(interpreter=Path(sys.executable), gmx=Path("/usr/bin/true"))

    output = tmp_path / "lig"
    (output / "GMX_PROLIG_MD").mkdir(parents=True)
    (output / "GMX_PROLIG_MD" / "topol.top").write_text("x", encoding="utf-8")
    (output / "GMX_PROLIG_MD" / "localrun.sh").write_text("x", encoding="utf-8")
    (output / Simulate.MANIFEST).write_text(
        json.dumps(
            {"build": {"built": True, "run_id": "lig", "arguments": {},
                       "inputs": {"protein_sha256": "aa", "ligand_sha256": "bb"}}}
        ),
        encoding="utf-8",
    )

    assert Simulate._reusable(runner, output, "aa", "bb") is not None
    # A different ligand in the same directory is not the same build.
    assert Simulate._reusable(runner, output, "aa", "cc") is None
    assert Simulate._reusable(runner, output, "zz", "bb") is None
