#!/usr/bin/env python3
"""Try to falsify the claim that a PRISM build is a pure function of its inputs.

This is a falsification test, not a feature, and it is the first thing ETALON had to
run. The whole question of whether PRISM's build steps can become content-addressed
MolCascade stages turns on it: a stage declared ``DETERMINISTIC`` is cached
unconditionally, and MolCascade never verifies that a plugin honours the promise its
descriptor makes. So a wrong answer here does not fail loudly -- it produces a cache
whose key asserts reproducibility for a result that was drawn from the clock, and
every measurement taken afterwards inherits the assertion.

Two levels, because the toolchain for a full build is rarely all present at once.

``--genion`` isolates the one command that was suspected and needs GROMACS alone. It
builds a water box, runs ``gmx grompp``, then runs ``gmx genion`` twice with exactly
the argument list PRISM constructs (``prism/utils/system/solvation.py:138-156``) in
two fresh directories, and compares the bytes.

``--build`` runs a real protein-ligand build twice through PRISM into two fresh
absolute paths and compares every emitted file. It needs AmberTools as well, and it
reports which files differ rather than only whether any did -- because the remedy
depends on which: an ion position is physics and must be seeded, while a timestamp in
a topology header is provenance and can be normalised out of a digest.

Findings are printed as a verdict and written as JSON, because a falsification result
that is not recorded gets re-argued.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

#: The argument list PRISM builds, minus the paths. Kept in this shape so that a
#: change upstream shows up as a diff against a literal rather than as a silent
#: divergence between what is tested and what runs.
GENION_ARGS = ("-pname", "NA", "-nname", "CL", "-neutral", "-conc", "0.15")

MINIMAL_TOPOLOGY = """\
#include "amber14sb.ff/forcefield.itp"
#include "amber14sb.ff/tip3p.itp"
#include "amber14sb.ff/ions.itp"
[ system ]
determinism probe
[ molecules ]
SOL  {waters}
"""

IONS_MDP = "integrator = steep\nnsteps = 1\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess:
    # Bytes rather than text: GROMACS writes output no codec claims, and a decode
    # error here would look like a failed probe rather than a noisy one.
    return subprocess.run(
        command,
        cwd=str(cwd),
        input=None if stdin is None else stdin.encode(),
        capture_output=True,
        check=False,
    )


def gmx() -> str:
    found = shutil.which("gmx") or shutil.which("gmx_mpi")
    if not found:
        raise SystemExit(
            "no gmx on PATH. This probe needs GROMACS; source its GMXRC or add its bin "
            "directory to PATH."
        )
    return found


def probe_genion(workspace: Path) -> dict[str, object]:
    """Run the one command PRISM calls without a seed, twice, and compare."""

    binary = gmx()
    workspace.mkdir(parents=True, exist_ok=True)
    box = workspace / "box.gro"

    made = run([binary, "solvate", "-cs", "spc216.gro", "-box", "3", "3", "3", "-o", str(box)], cwd=workspace)
    if not box.is_file():
        return {
            "probe": "genion",
            "status": "SETUP_FAILED",
            "detail": made.stderr.decode("utf-8", "replace")[-1500:],
        }
    waters = box.read_text(encoding="utf-8", errors="replace").count("OW")
    (workspace / "topol.top").write_text(MINIMAL_TOPOLOGY.format(waters=waters), encoding="utf-8")
    (workspace / "ions.mdp").write_text(IONS_MDP, encoding="utf-8")

    grompp = run(
        [binary, "grompp", "-f", "ions.mdp", "-c", "box.gro", "-p", "topol.top",
         "-o", "ions.tpr", "-maxwarn", "5"],
        cwd=workspace,
    )
    if not (workspace / "ions.tpr").is_file():
        return {
            "probe": "genion",
            "status": "SETUP_FAILED",
            "detail": grompp.stderr.decode("utf-8", "replace")[-1500:],
        }

    digests, ion_counts = {}, {}
    for label in ("a", "b"):
        trial = workspace / label
        trial.mkdir(exist_ok=True)
        shutil.copy2(workspace / "topol.top", trial / "topol.top")
        run(
            [binary, "genion", "-s", "../ions.tpr", "-o", "ions.gro", "-p", "topol.top",
             *GENION_ARGS],
            cwd=trial,
            stdin="SOL\n",
        )
        produced = trial / "ions.gro"
        if not produced.is_file():
            return {"probe": "genion", "status": "SETUP_FAILED", "detail": f"trial {label} produced no output"}
        digests[label] = sha256_file(produced)
        text = produced.read_text(encoding="utf-8", errors="replace")
        # Count lines, not substring hits: a .gro atom line carries the residue name
        # and the atom name, so counting occurrences double-counts every ion.
        ion_counts[label] = sum(
            1
            for line in text.splitlines()
            if len(line) > 15 and line[5:10].strip() in ("NA", "CL")
        )

    identical = digests["a"] == digests["b"]
    return {
        "probe": "genion",
        "status": "DETERMINISTIC" if identical else "NOT_DETERMINISTIC",
        "waters": waters,
        "sha256": digests,
        "ion_counts": ion_counts,
        "command": ["gmx", "genion", "-s", "<tpr>", "-o", "<gro>", "-p", "<top>", *GENION_ARGS],
        "seed_passed": False,
        "finding": (
            "Identical bytes from two runs, which would mean the command is seeded "
            "somewhere this probe cannot see."
            if identical
            else "Different bytes from identical inputs. genion replaces randomly chosen "
            "solvent molecules and PRISM passes no -seed, so ion positions differ "
            "between two builds of the same system. The build is not a pure function "
            "of its inputs, and a stage wrapping it cannot honestly declare "
            "DETERMINISTIC."
        ),
    }


def probe_build(protein: Path, ligand: Path, workspace: Path) -> dict[str, object]:
    """Run a real build twice into fresh absolute paths and compare every file."""

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "asset" / "prism"))
    try:
        import prism  # noqa: F401
    except ImportError as error:
        return {"probe": "build", "status": "SETUP_FAILED", "detail": str(error)}
    for tool in ("antechamber", "acpype", "parmchk2"):
        if shutil.which(tool) is None:
            return {
                "probe": "build",
                "status": "SKIPPED",
                "detail": (
                    f"{tool} is not on PATH. A GAFF build needs AmberTools; run this probe "
                    "in the environment that has it, which is also the environment PRISM's "
                    "own server runs in."
                ),
            }

    trees: dict[str, dict[str, str]] = {}
    for label in ("a", "b"):
        output = workspace / label
        if output.exists():
            shutil.rmtree(output)
        # A fresh absolute directory each time: PRISM's build steps skip when their
        # product exists, so reusing a directory would measure the skip rather than
        # the build.
        # A separate interpreter per build, so the second is not affected by anything the
        # first left in module state -- and PRISM's build mutates cwd on some failure paths.
        program = (
            "import prism as pm, sys; "
            "s = pm.system(sys.argv[1], sys.argv[2], output_dir=sys.argv[3]); "
            "s.build()"
        )
        completed = subprocess.run(
            [sys.executable, "-c", program, str(protein), str(ligand), str(output)],
            capture_output=True,
            check=False,
        )
        if not output.is_dir():
            return {
                "probe": "build",
                "status": "SETUP_FAILED",
                "detail": completed.stderr.decode("utf-8", "replace")[-2000:],
            }
        trees[label] = {
            str(path.relative_to(output).as_posix()): sha256_file(path)
            for path in sorted(output.rglob("*"))
            if path.is_file()
        }

    only_a = sorted(set(trees["a"]) - set(trees["b"]))
    only_b = sorted(set(trees["b"]) - set(trees["a"]))
    shared = sorted(set(trees["a"]) & set(trees["b"]))
    differing = [name for name in shared if trees["a"][name] != trees["b"][name]]
    return {
        "probe": "build",
        "status": "DETERMINISTIC" if not (differing or only_a or only_b) else "NOT_DETERMINISTIC",
        "files_compared": len(shared),
        "files_only_in_a": only_a,
        "files_only_in_b": only_b,
        "files_differing": differing,
        "finding": (
            "Every emitted file matched."
            if not (differing or only_a or only_b)
            else f"{len(differing)} of {len(shared)} files differ between two builds of "
            "the same inputs. Which files differ decides the remedy: ion positions and "
            "charges are physics and must be seeded upstream, while timestamps and "
            "absolute paths in headers are provenance and can be normalised out of a "
            "digest without changing what was built."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--genion", action="store_true", help="Isolate gmx genion. Needs GROMACS only.")
    parser.add_argument("--build", action="store_true", help="Run a full PRISM build twice. Needs AmberTools.")
    parser.add_argument("--protein", type=Path, help="Protein PDB for --build")
    parser.add_argument("--ligand", type=Path, help="Ligand MOL2/SDF for --build")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("/tmp/etalon-determinism"),
        help="Scratch directory. Wiped per probe.",
    )
    parser.add_argument("--json", type=Path, help="Write findings here as well as printing them.")
    arguments = parser.parse_args()

    if not (arguments.genion or arguments.build):
        arguments.genion = True

    findings: list[dict[str, object]] = []
    if arguments.genion:
        root = arguments.workspace / "genion"
        if root.exists():
            shutil.rmtree(root)
        findings.append(probe_genion(root))
    if arguments.build:
        if not (arguments.protein and arguments.ligand):
            findings.append(
                {"probe": "build", "status": "SETUP_FAILED", "detail": "--build needs --protein and --ligand"}
            )
        else:
            findings.append(probe_build(arguments.protein, arguments.ligand, arguments.workspace / "build"))

    failed = 0
    for finding in findings:
        print(f"\n{finding['probe']}: {finding['status']}")
        for key in ("waters", "files_compared", "ion_counts", "sha256"):
            if key in finding:
                print(f"  {key}: {finding[key]}")
        if finding.get("files_differing"):
            differing = finding["files_differing"]
            assert isinstance(differing, list)
            print(f"  differing files ({len(differing)}):")
            for name in differing[:20]:
                print(f"    {name}")
            if len(differing) > 20:
                print(f"    ... and {len(differing) - 20} more")
        if finding.get("detail"):
            print(f"  detail: {finding['detail']}")
        print(f"  {finding.get('finding', '')}")
        if finding["status"] == "NOT_DETERMINISTIC":
            failed += 1

    if arguments.json:
        arguments.json.write_text(json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\nwritten to {arguments.json}")

    print()
    if failed:
        print(
            f"{failed} probe(s) falsified the determinism premise. A stage wrapping this "
            "work must not declare DETERMINISTIC; see docs/adr/0001."
        )
        # Exit 0 deliberately: a falsification is a successful measurement, not a
        # failed test run. Read the status field to decide anything.
    else:
        print("No probe falsified the premise in this environment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
