"""The shim must do one thing and pass everything else through untouched.

That asymmetry is the entire justification for not patching PRISM, so it is what these
tests protect. A shim that quietly did more than insert a missing seed would be a
modification of PRISM's behaviour wearing a different hat, and the asset digest would
still verify -- which is exactly the kind of silent change ETALON exists to make
impossible.

The determinism tests need GROMACS and skip without it. The pass-through and refusal
tests do not: they drive the shim with a fake binary that prints its own argv, which is
a more exact check than a real GROMACS run because it compares the argument list itself
rather than a downstream consequence of it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.boundary import toolchain as T

_HAS_GMX = shutil.which("gmx") is not None or shutil.which("gmx_mpi") is not None


@pytest.fixture
def echo_binary(tmp_path: Path) -> Path:
    """A stand-in for gmx that reports exactly what it was called with."""

    binary = tmp_path / "fake_gmx"
    binary.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8"
    )
    binary.chmod(0o755)
    return binary


def _call(shim: Path, arguments: list[str], *, seed: str | None) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    if seed is None:
        environment.pop(T.SEED_VARIABLE, None)
    else:
        environment[T.SEED_VARIABLE] = seed
    return subprocess.run(
        [str(shim / "gmx"), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def test_a_command_that_is_not_genion_passes_through_unchanged(
    tmp_path: Path, echo_binary: Path
) -> None:
    """The property that makes not-patching-PRISM defensible."""

    shim = T.install(tmp_path / "shim", real_gmx=echo_binary)
    arguments = ["grompp", "-f", "md.mdp", "-c", "conf.gro", "-p", "topol.top", "-maxwarn", "5"]

    result = _call(shim.shim_dir, arguments, seed="7")

    assert result.returncode == 0
    assert result.stdout.splitlines() == arguments


def test_an_argument_containing_spaces_survives(tmp_path: Path, echo_binary: Path) -> None:
    """GROMACS paths contain spaces, and a shim that re-splits them corrupts a run."""

    shim = T.install(tmp_path / "shim", real_gmx=echo_binary)
    arguments = ["mdrun", "-deffnm", "/a path/with spaces/md", "-maxh", "23.5"]

    result = _call(shim.shim_dir, arguments, seed="7")

    assert result.stdout.splitlines() == arguments


def test_genion_without_a_seed_gets_one(tmp_path: Path, echo_binary: Path) -> None:
    shim = T.install(tmp_path / "shim", real_gmx=echo_binary)

    result = _call(
        shim.shim_dir,
        ["genion", "-s", "ions.tpr", "-o", "ions.gro", "-p", "topol.top", "-neutral"],
        seed="20260911",
    )

    lines = result.stdout.splitlines()
    assert lines[:3] == ["genion", "-seed", "20260911"]
    # Every original argument is still there, in order, after the inserted pair.
    assert lines[3:] == ["-s", "ions.tpr", "-o", "ions.gro", "-p", "topol.top", "-neutral"]


def test_genion_that_already_has_a_seed_is_not_touched(
    tmp_path: Path, echo_binary: Path
) -> None:
    """A caller that made the decision keeps it. Two seeds would be a silent override."""

    shim = T.install(tmp_path / "shim", real_gmx=echo_binary)
    arguments = ["genion", "-s", "ions.tpr", "-seed", "42", "-neutral"]

    result = _call(shim.shim_dir, arguments, seed="20260911")

    assert result.stdout.splitlines() == arguments
    assert result.stdout.count("-seed") == 1


def test_genion_with_no_seed_available_refuses_rather_than_proceeding(
    tmp_path: Path, echo_binary: Path
) -> None:
    """Failing closed. Letting it through would restore exactly the silence ADR 0001 is about."""

    shim = T.install(tmp_path / "shim", real_gmx=echo_binary)

    result = _call(shim.shim_dir, ["genion", "-s", "ions.tpr"], seed=None)

    assert result.returncode == 64
    assert "clock" in result.stderr
    assert "ADR 0001" in result.stderr
    assert not result.stdout, "the real binary must not have run"


def test_the_digest_describes_what_is_on_disk(tmp_path: Path, echo_binary: Path) -> None:
    """A recorded digest that does not match the file would make every job record wrong."""

    import hashlib

    shim = T.install(tmp_path / "shim", real_gmx=echo_binary)
    observed = hashlib.sha256((shim.shim_dir / "gmx").read_bytes()).hexdigest()

    assert observed == shim.shim_sha256


def test_reinstalling_replaces_a_stale_shim(tmp_path: Path, echo_binary: Path) -> None:
    """A shim from an older ETALON must not survive and be reported as the current one."""

    target = tmp_path / "shim"
    first = T.install(target, real_gmx=echo_binary)
    (target / "gmx").write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")

    second = T.install(target, real_gmx=echo_binary)

    assert second.shim_sha256 == first.shim_sha256
    assert _call(target, ["version"], seed="1").returncode == 0


def test_a_symlink_is_refused(tmp_path: Path, echo_binary: Path) -> None:
    target = tmp_path / "shim"
    target.mkdir()
    (target / "gmx").symlink_to(echo_binary)

    with pytest.raises(T.ToolchainError, match="symlink"):
        T.install(target, real_gmx=echo_binary)


def test_locate_refuses_to_wrap_another_shim(tmp_path: Path, echo_binary: Path) -> None:
    """A shim that execs a shim is a loop whose symptom is a process that never starts."""

    shim = T.install(tmp_path / "shim", real_gmx=echo_binary)
    environment = dict(os.environ)
    environment["PATH"] = f"{shim.shim_dir}{os.pathsep}{environment.get('PATH', '')}"

    original = os.environ.get("PATH")
    try:
        os.environ["PATH"] = environment["PATH"]
        # Either it finds the real gmx further down the path, or it raises -- what it must
        # not do is return the shim.
        try:
            found = T.locate_gmx()
        except T.ToolchainError:
            return
        assert found != shim.shim_dir / "gmx"
        assert b"ETALON determinism shim" not in found.read_bytes()[:400]
    finally:
        if original is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original


def test_active_distinguishes_a_shimmed_environment(tmp_path: Path, echo_binary: Path) -> None:
    shim = T.install(tmp_path / "shim", real_gmx=echo_binary)

    assert T.active(shim.environment(seed=1)) is True
    assert T.active({"PATH": "/usr/bin:/bin"}) is False
    assert T.active({}) is False


def test_the_environment_preserves_what_gromacs_needs(tmp_path: Path, echo_binary: Path) -> None:
    """Starting from an empty environment would be cleaner and would not run."""

    shim = T.install(tmp_path / "shim", real_gmx=echo_binary)

    environment = shim.environment(seed=5, base={"GMXLIB": "/opt/ff", "PATH": "/usr/bin"})

    assert environment["GMXLIB"] == "/opt/ff"
    assert environment["PATH"].startswith(str(shim.shim_dir))
    assert environment["PATH"].endswith("/usr/bin")
    assert environment[T.SEED_VARIABLE] == "5"


@pytest.mark.skipif(not _HAS_GMX, reason="GROMACS is not installed")
def test_the_same_seed_gives_the_same_ions_and_a_different_seed_does_not(tmp_path: Path) -> None:
    """The measurement ADR 0001 rests on, as a regression test.

    Both halves matter. Identical bytes under one seed show the placement is reproducible;
    different bytes under another show the seed is genuinely consumed rather than accepted
    and ignored, which a shim could get wrong in a way the first half would not catch.
    """

    import hashlib

    shim = T.install(tmp_path / "shim")
    binary = str(shim.shim_dir / "gmx")
    work = tmp_path / "work"
    work.mkdir()

    def gmx(arguments: list[str], *, cwd: Path, seed: int, stdin: str | None = None) -> None:
        subprocess.run(
            [binary, *arguments],
            cwd=str(cwd),
            env=shim.environment(seed=seed),
            input=None if stdin is None else stdin.encode(),
            capture_output=True,
            check=False,
        )

    gmx(["solvate", "-cs", "spc216.gro", "-box", "3", "3", "3", "-o", "box.gro"], cwd=work, seed=1)
    box = work / "box.gro"
    if not box.is_file():
        pytest.skip("this GROMACS installation has no spc216.gro to solvate with")
    waters = box.read_text(encoding="utf-8", errors="replace").count("OW")
    (work / "topol.top").write_text(
        '#include "amber14sb.ff/forcefield.itp"\n'
        '#include "amber14sb.ff/tip3p.itp"\n'
        '#include "amber14sb.ff/ions.itp"\n'
        "[ system ]\nshim regression\n[ molecules ]\n"
        f"SOL  {waters}\n",
        encoding="utf-8",
    )
    (work / "ions.mdp").write_text("integrator = steep\nnsteps = 1\n", encoding="utf-8")
    gmx(
        ["grompp", "-f", "ions.mdp", "-c", "box.gro", "-p", "topol.top", "-o", "ions.tpr", "-maxwarn", "5"],
        cwd=work,
        seed=1,
    )
    if not (work / "ions.tpr").is_file():
        pytest.skip("this GROMACS installation lacks the amber14sb force field")

    digests = {}
    for label, seed in (("a", 20260911), ("b", 20260911), ("c", 999)):
        trial = work / label
        trial.mkdir()
        shutil.copy2(work / "topol.top", trial / "topol.top")
        gmx(
            ["genion", "-s", "../ions.tpr", "-o", "ions.gro", "-p", "topol.top",
             "-pname", "NA", "-nname", "CL", "-neutral", "-conc", "0.15"],
            cwd=trial,
            seed=seed,
            stdin="SOL\n",
        )
        produced = trial / "ions.gro"
        assert produced.is_file(), f"trial {label} produced nothing"
        digests[label] = hashlib.sha256(produced.read_bytes()).hexdigest()

    assert digests["a"] == digests["b"], "the same seed did not reproduce the placement"
    assert digests["a"] != digests["c"], "the seed was accepted and ignored"
