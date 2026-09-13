"""Drive PRISM as the campaign's expensive stage, and know whether it worked.

``screen.py`` is thin because MolCascade supplies the discipline: it compiles to a
revision id before running, commits content-addressed artifacts, validates its contracts
and resumes by verification. This file is not thin, and the difference in length between
the two is the finding rather than an accident. PRISM builds correct systems and keeps no
record of having built them: no run identity, no manifest, no digest of what it consumed,
and a driver script that cannot fail.

Four things are supplied here, all from outside, because the constraint is that PRISM's
own behaviour does not change.

**A run identity and a manifest.** ETALON assigns the run id and writes beside the build
everything needed to reconstruct it: the two asset commits, the exact arguments, the seed,
the resolved ``gmx`` and its version, the interpreter that ran it, and the digests of the
protein and ligand that went in. PRISM writes none of this, so two builds of different
ligands into two directories are, afterwards, two directories.

**A subprocess in a named environment.** This is not indirection for its own sake. PRISM's
build needs AmberTools -- antechamber, parmchk2, acpype, tleap -- and in this installation
those live in a different conda environment from the one MolCascade runs in; ``gmx`` in
turn is on a system path in neither. So the expensive stage cannot be an import. Crossing
a process boundary also buys the two things the record needs: the child's output can be
captured in full, and nothing the build does to the interpreter reaches the agent.

**Failure detection by product, not by exit code.** ``localrun.sh`` has no ``set -e``, so
a failed ``grompp`` writes no tpr, ``mdrun`` fails on the missing file, and the next stage
runs ``grompp -c ./em/em.gro`` against a file that was never written -- every stage fails
in turn. What the caller sees was measured on this machine rather than assumed, and it is
three different things:

- A fresh build with a broken topology exits **1**. The last command in the script is
  production's ``mdrun``, and it failed too.
- The same directory re-driven after production had completed once exits **0**, with em,
  nvt and npt all failed and 23 error lines in the log. The final block takes its
  ``if [ -f ./prod/md.gro ]`` skip branch, ``echo`` succeeds, and that is the script's exit
  status. This is not a contrived case: it is what re-driving any directory looks like.
- A run killed by a wall-clock limit has **no exit code at all**, which is the common case,
  because PRISM's default production length is 500 ns and a campaign that wanted
  equilibration will always hit the limit.

One bit about the last command cannot distinguish those, and none of them says *which*
stages finished. The products do, and the fix needs no change to PRISM because the script
already states the predicate: every stage is written as ``if [ -f ./em/em.gro ]`` skip,
``elif [ -f ./em/em.tpr ]`` resume, else build. "Did this stage finish" is therefore
*already* defined as "does its product exist", so checking the same files afterwards cannot
disagree with PRISM's own notion of completion.

**The warnings PRISM tells grompp to ignore.** Every ``grompp`` in the script carries
``-maxwarn 999``, so no warning ever stops a build. The warnings are still printed, and an
agent that claims to know whether a system was sound cannot discard them. They are
captured, counted and classified here. Note what this is not: it is not a proposal to
change the flag. ``-maxwarn`` decides what PRISM is willing to build, which is a property
of the protocol and belongs to whoever owns it. Reading the warnings is this layer's
business; overruling them is not.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from etalon.boundary.infra import Infra, load

#: Tools a gaff2 protein-ligand build cannot proceed without. Probed rather than assumed,
#: because in this installation they are not in the environment that runs the screen.
REQUIRED_TOOLS = ("antechamber", "parmchk2", "acpype", "tleap")

#: What each stage of ``localrun.sh`` leaves behind when it finishes, relative to
#: ``GMX_PROLIG_MD``. Taken from the script's own skip conditions, so this layer's notion
#: of "finished" is the same one PRISM resumes on and the two cannot drift apart.
STAGE_PRODUCTS: tuple[tuple[str, str, str], ...] = (
    ("em", "em/em.tpr", "em/em.gro"),
    ("nvt", "nvt/nvt.tpr", "nvt/nvt.gro"),
    ("npt", "npt/npt.tpr", "npt/npt.gro"),
    ("prod", "prod/md.tpr", "prod/md.gro"),
)

#: grompp prints these and PRISM tells it to ignore them. Classified rather than counted
#: alone, because the remedy differs: a charge that is not an integer is a parameterisation
#: error and a missing-atomtype note usually is not.
_WARNING_PATTERNS: tuple[tuple[str, str], ...] = (
    ("non_integer_charge", r"non[- ]?integer.{0,24}charge|System has non-zero total charge"),
    ("atom_name_mismatch", r"atom name.{0,40}(does not match|not found)"),
    ("missing_parameters", r"No default .* types|Unknown bond_atomtype|could not find"),
    ("coordinates_missing", r"did not find a matching entry|Atom .* not found"),
    ("large_forces", r"has large.{0,16}force|LINCS WARNING|1-4 interaction"),
)
#: grompp's own warning header: ``WARNING 1 [file topol.top, line 42]:``. The number or the
#: bracket is required. Without it the pattern matched Python logging -- measured on a real
#: build log, it reported four grompp warnings where there were none, two of them
#: ``WARNING:root:alchemlyb not available``. A warning count that includes a missing
#: optional dependency is worse than no count, because it is read as the system builder
#: complaining about the system.
_WARNING_LINE = re.compile(r"^\s*WARNING\s+(?:\d+|\[)", re.IGNORECASE | re.MULTILINE)


class SimulationError(RuntimeError):
    """Raised when the expensive stage cannot honestly be started."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class Environment:
    """Where the expensive stage will actually run, probed rather than assumed."""

    interpreter: Path
    gmx: Path | None
    #: Tools from :data:`REQUIRED_TOOLS` that could not be found. A build is refused when
    #: this is non-empty, with the names, because "PRISM failed" an hour in is a much
    #: worse message than "antechamber is not on the path of that interpreter".
    missing: tuple[str, ...] = ()
    #: Directory holding ETALON's ``gmx`` shim, when one is installed. With it, ion
    #: placement is seeded and the build is reproducible; without it the build is fine
    #: and no claim that it can be re-derived is.
    shim_dir: Path | None = None
    seed: int | None = None
    gmx_version: str = ""

    @property
    def ready(self) -> bool:
        return not self.missing and self.gmx is not None

    @property
    def reproducible(self) -> bool:
        return self.shim_dir is not None and self.seed is not None

    def exported(self, *, asset_root: Path) -> dict[str, str]:
        """The child's environment: the pinned asset importable, the shim ahead of gmx."""

        from etalon.boundary.toolchain import SEED_VARIABLE

        parts = [str(self.interpreter.parent)]
        if self.shim_dir is not None:
            # Ahead of the real gmx, which is the entire mechanism.
            parts.insert(0, str(self.shim_dir))
        if self.gmx is not None:
            parts.append(str(self.gmx.parent))
        parts.append(os.environ.get("PATH", ""))

        child = dict(os.environ)
        child["PATH"] = os.pathsep.join(part for part in parts if part)
        # The pinned tree, not whatever the child interpreter has installed. Without this
        # the manifest would cite a commit and the build would be done by something else.
        child["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(asset_root), child.get("PYTHONPATH", "")) if part
        )
        if self.seed is not None:
            child[SEED_VARIABLE] = str(self.seed)
        return child

    def as_dict(self) -> dict[str, object]:
        return {
            "interpreter": str(self.interpreter),
            "gmx": None if self.gmx is None else str(self.gmx),
            "gmx_version": self.gmx_version,
            "missing_tools": list(self.missing),
            "ready": self.ready,
            "reproducible": self.reproducible,
            "shim_dir": None if self.shim_dir is None else str(self.shim_dir),
            "seed": self.seed,
        }


def discover(
    interpreter: str | Path | None = None,
    *,
    seed: int | None = None,
    shim_dir: str | Path | None = None,
    search: Iterable[str] | None = None,
) -> Environment:
    """Probe an environment for everything a build needs, and report what is absent.

    Args:
        interpreter: The Python that will run PRISM. Defaults to this one, which in this
            installation is usually wrong -- the screen's environment has no AmberTools --
            and the resulting :attr:`Environment.missing` is the point rather than a
            failure.
        search: Extra directories to look in, ahead of ``PATH``. Use it to name the
            environment's ``bin`` when the tools are not on this process's path.
    """

    chosen = Path(interpreter or sys.executable).expanduser().resolve()
    if not chosen.is_file():
        raise SimulationError(f"no interpreter at {chosen}")

    extra = [str(Path(item).expanduser()) for item in (search or ())]
    # The interpreter's own bin first: a conda environment keeps its tools beside its
    # python, and that is the association being relied on.
    lookup = os.pathsep.join([*extra, str(chosen.parent), os.environ.get("PATH", "")])

    missing = tuple(tool for tool in REQUIRED_TOOLS if shutil.which(tool, path=lookup) is None)
    found_gmx = shutil.which("gmx", path=lookup) or shutil.which("gmx_mpi", path=lookup)
    gmx = Path(found_gmx).resolve() if found_gmx else None

    version = ""
    if gmx is not None:
        probe = subprocess.run(
            [str(gmx), "--version"], capture_output=True, text=True, check=False, timeout=120
        )
        # The banner, not the first line containing "version" -- that matched the echoed
        # command line in GROMACS's own header and recorded "gmx --version" as the version.
        # Matched on the banner's own shape so a future layout change fails visibly rather
        # than quietly recording something else.
        banner = re.search(r"GROMACS\s*-\s*\S+,\s*([0-9][^\s(]*)", probe.stdout)
        if banner is None:
            banner = re.search(r"^GROMACS version:\s*(\S+)", probe.stdout, re.MULTILINE)
        version = banner.group(1).strip() if banner else "unreadable"
    return Environment(
        interpreter=chosen,
        gmx=gmx,
        missing=missing,
        shim_dir=None if shim_dir is None else Path(shim_dir).expanduser().resolve(),
        seed=seed,
        gmx_version=version,
    )


@dataclass(frozen=True, slots=True)
class Materialized:
    """One handoff record written to a file PRISM can read, and checked against it."""

    parent_id: str
    path: Path | None
    #: Why this record produced no file. Empty when it did.
    refused: str = ""

    @property
    def usable(self) -> bool:
        return self.path is not None and not self.refused


def materialize_ligands(
    rows: Sequence[Mapping[str, object]],
    directory: str | Path,
) -> tuple[Materialized, ...]:
    """Write each handoff record's geometry to an SDF, and verify the file matches it.

    This is the seam, and it is the one place in the campaign where a claim and a file have
    to be made to agree. The contract carries a molblock; PRISM reads files. The obvious
    implementation -- rebuild the molecule from ``stereo_smiles`` and write that -- is
    exactly the defect the contract was introduced to stop: it is how a drawing reaches a
    force field. So the molblock is written through byte for byte, with the ``$$$$``
    terminator appended and nothing else touched. No parse, no re-embed, no round trip,
    because a round trip is an opportunity to lose a coordinate.

    Then the file is read back and compared with what the record declared. If the heavy
    atom count, the hydrogen count or the formal charge on disk differs from the row, the
    record has stopped describing the file and the molecule is refused rather than handed
    over -- a mismatch here would mean every later check was performed against a
    description of something else.
    """

    from rdkit import Chem, RDLogger

    RDLogger.DisableLog("rdApp.*")
    target = Path(directory).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    out: list[Materialized] = []
    for index, row in enumerate(rows):
        identifier = str(row.get("parent_id") or f"row:{index}")
        molblock = row.get("molblock")
        if not molblock:
            out.append(
                Materialized(identifier, None, "the record carries no molblock")
            )
            continue
        if str(row.get("status") or "") != "OK":
            out.append(
                Materialized(
                    identifier,
                    None,
                    f"the producer reported status={row.get('status')!r}",
                )
            )
            continue

        # A digest-shaped parent_id is not a filename. Truncated to its hex tail with the
        # index kept, so two molecules cannot collide and the name is still traceable.
        stem = f"{index:05d}_{identifier.rsplit(':', 1)[-1][:16]}"
        path = target / f"{stem}.sdf"
        text = str(molblock)
        path.write_text(
            text if text.endswith("\n") else text + "\n",
            encoding="utf-8",
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write("$$$$\n")

        molecule = Chem.MolFromMolFile(str(path), removeHs=False, sanitize=False)
        if molecule is None:
            out.append(
                Materialized(
                    identifier, None, "the molblock in the record does not parse as a MOL file"
                )
            )
            continue
        heavy = sum(1 for atom in molecule.GetAtoms() if atom.GetAtomicNum() > 1)
        hydrogens = sum(1 for atom in molecule.GetAtoms() if atom.GetAtomicNum() == 1)
        charge = Chem.GetFormalCharge(molecule)
        declared = (
            int(row.get("heavy_atom_count") or -1),
            int(row.get("hydrogen_count") or -1),
            int(row.get("formal_charge") or 0),
        )
        if (heavy, hydrogens, charge) != declared:
            out.append(
                Materialized(
                    identifier,
                    None,
                    f"the file holds {heavy} heavy atoms, {hydrogens} hydrogens and charge "
                    f"{charge}; the record declares {declared[0]}, {declared[1]} and "
                    f"{declared[2]}. Every check this campaign ran was against the record, "
                    "so a file that differs from it has not been checked at all.",
                )
            )
            continue
        out.append(Materialized(identifier, path))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class StageStatus:
    """One stage of the driver script, judged by its products."""

    stage: str
    tpr: bool
    product: bool

    @property
    def state(self) -> str:
        if self.product:
            return "FINISHED"
        if self.tpr:
            # grompp worked and mdrun did not: the interesting failure, because the
            # system was buildable and the simulation was not.
            return "STARTED_NOT_FINISHED"
        return "NEVER_STARTED"

    def as_dict(self) -> dict[str, object]:
        return {"stage": self.stage, "state": self.state, "tpr": self.tpr, "product": self.product}


@dataclass(frozen=True, slots=True)
class Warnings:
    """What grompp said while being told not to stop."""

    total: int
    by_kind: dict[str, int] = field(default_factory=dict)
    samples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "grompp_warnings": self.total,
            "by_kind": dict(self.by_kind),
            "samples": list(self.samples),
            "note": (
                "Every grompp in PRISM's driver carries -maxwarn 999, so none of these "
                "stopped the build. They are recorded because a campaign that reports a "
                "binding energy should be able to say what the system builder complained "
                "about."
            ),
        }


def read_warnings(text: str, *, keep: int = 5) -> Warnings:
    """Count and classify grompp's warnings out of captured output."""

    lines = text.splitlines()
    flagged = [line.strip() for line in lines if _WARNING_LINE.match(line)]
    by_kind: dict[str, int] = {}
    for kind, pattern in _WARNING_PATTERNS:
        hits = len(re.findall(pattern, text, re.IGNORECASE))
        if hits:
            by_kind[kind] = hits
    return Warnings(total=len(flagged), by_kind=by_kind, samples=tuple(flagged[:keep]))


#: gmx_MMPBSA's result lines: ``DELTA TOTAL = -35.2 +/- 2.1``. Taken verbatim from PRISM's
#: own parser at prism/mcp/analysis.py:397 so the two readings of one file cannot disagree.
_ENERGY_LINE = re.compile(r"^\s*([\w\s/]+?)\s*=\s*([-\d.]+)\s*\+/-\s*([-\d.]+)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class BindingEnergy:
    """A binding free energy read from a finished MM-PBSA calculation.

    ``spread`` is the figure gmx_MMPBSA prints after ``+/-``, and it is recorded under that
    name rather than as an uncertainty on purpose. It is the standard deviation across the
    frames of a single trajectory, so it describes how much the value moved during one run
    and says nothing about how much it would move in another -- which is the quantity a
    campaign comparing two ligands actually needs. Ensemble work on this exact estimator
    finds the run-to-run spread considerably larger than the within-run one, so treating
    this number as an error bar systematically overstates what a single run established.
    """

    total_kcal_mol: float
    spread_kcal_mol: float
    components: dict[str, float]
    source: Path

    def as_dict(self) -> dict[str, object]:
        return {
            "delta_g_bind_kcal_mol": self.total_kcal_mol,
            "within_run_spread_kcal_mol": self.spread_kcal_mol,
            "components_kcal_mol": dict(self.components),
            "source": str(self.source),
            "spread_is_not_an_uncertainty": (
                "The +/- figure is the standard deviation over one trajectory's frames. It "
                "is not an error bar on the binding free energy and must not be used as "
                "one when comparing two ligands."
            ),
        }


def read_binding_energy(mmpbsa_dir: str | Path) -> BindingEnergy | None:
    """Read ``FINAL_RESULTS_MMPBSA.dat``, or return ``None`` when there is nothing to read.

    ``None`` rather than an exception, because "the calculation has not produced a result"
    is an ordinary state of a campaign in progress and the admissibility layer already has
    a name for it: a measurement with no expensive value is withheld, with the reason
    recorded.
    """

    directory = Path(mmpbsa_dir).expanduser()
    path = directory / "FINAL_RESULTS_MMPBSA.dat"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    components = {
        match.group(1).strip(): float(match.group(2)) for match in _ENERGY_LINE.finditer(text)
    }
    total = next(
        (match for match in _ENERGY_LINE.finditer(text) if match.group(1).strip() == "DELTA TOTAL"),
        None,
    )
    if total is None:
        return None
    return BindingEnergy(
        total_kcal_mol=float(total.group(2)),
        spread_kcal_mol=float(total.group(3)),
        components=components,
        source=path,
    )


@dataclass(frozen=True, slots=True)
class BuildRecord:
    """The run identity and manifest PRISM does not keep."""

    run_id: str
    output_dir: Path
    md_dir: Path
    protein_sha256: str
    ligand_sha256: str
    arguments: dict[str, object]
    environment: Environment
    infra: dict[str, object]
    built: bool
    detail: str = ""
    stdout_path: Path | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "at": _now(),
            "output_dir": str(self.output_dir),
            "md_dir": str(self.md_dir),
            "inputs": {
                "protein_sha256": self.protein_sha256,
                "ligand_sha256": self.ligand_sha256,
            },
            "arguments": dict(self.arguments),
            "environment": self.environment.as_dict(),
            "infra": dict(self.infra),
            "built": self.built,
            "detail": self.detail,
            "captured_output": None if self.stdout_path is None else str(self.stdout_path),
        }


@dataclass(frozen=True, slots=True)
class DriveRecord:
    """What running the driver produced, judged by products rather than exit code."""

    run_id: str
    #: The driver's own exit status, or ``None`` when the wall-clock limit killed it.
    #: Recorded, and used to decide nothing -- see :attr:`succeeded`.
    exit_code: int | None
    stages: tuple[StageStatus, ...]
    warnings: Warnings
    stdout_path: Path
    requested: tuple[str, ...]
    #: Set when the wall-clock limit killed the driver. Not a failure of the stages that
    #: had already finished, and the products say which those were.
    timed_out: bool = False

    @property
    def finished(self) -> tuple[str, ...]:
        return tuple(s.stage for s in self.stages if s.state == "FINISHED")

    @property
    def failed(self) -> tuple[StageStatus, ...]:
        return tuple(s for s in self.stages if s.stage in self.requested and s.state != "FINISHED")

    @property
    def succeeded(self) -> bool:
        """Whether every requested stage left its product behind.

        Deliberately not ``exit_code == 0``. Measured: a re-driven directory whose
        production had already completed exits 0 with em, nvt and npt all failed, because
        the final block's skip branch is the last command to run. And a timed-out run has
        no exit code at all. See this module's docstring for all three cases.
        """

        return not self.failed

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "succeeded": self.succeeded,
            "requested": list(self.requested),
            "finished": list(self.finished),
            "failed": [s.as_dict() for s in self.failed],
            "stages": [s.as_dict() for s in self.stages],
            "warnings": self.warnings.as_dict(),
            "captured_output": str(self.stdout_path),
            "note": (
                "Success is every requested stage's product existing. Measured on this "
                "machine: a fresh build with a broken topology exits 1; the same "
                "directory re-driven after production had completed exits 0 with em, nvt "
                "and npt all failed and 23 error lines in the log; a run killed by the "
                "wall clock has no exit code at all. One bit about the last command "
                "cannot tell those apart, and none of them says which stages finished."
            ),
        }


class Simulate:
    """PRISM, as the campaign's expensive stage, with a record around it."""

    MANIFEST = "etalon_run.json"

    def __init__(
        self,
        workspace: str | Path,
        environment: Environment,
        *,
        infra: Infra | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.environment = environment
        self.infra = infra or load("prism")

    # -- build --------------------------------------------------------------

    def build(
        self,
        protein: str | Path,
        ligand: str | Path,
        *,
        run_id: str,
        ligand_forcefield: str = "gaff2",
        forcefield: str = "amber14sb",
        protonation: str = "propka",
        gaussian_method: str | None = None,
        do_optimization: bool = False,
        reuse: bool = False,
        overwrite: bool = False,
        timeout: int = 14_400,
    ) -> BuildRecord:
        """Build one protein-ligand system, and write the manifest PRISM does not.

        The defaults are PRISM's canonical ones, and box, salt and temperature are not
        touched at all: those belong to the protocol, not to an orchestration layer. What
        is added is identity.

        Args:
            gaussian_method: ``None`` uses AM1-BCC charges, which need no external QM and
                take seconds. ``"hf"`` (HF/6-31G*) or ``"dft"`` (B3LYP/6-31G*) compute RESP
                charges through Gaussian and are the better charges; on a 70-heavy-atom
                ligand they are also hours rather than seconds, per molecule. A campaign
                screening a shortlist and one validating a single lead should not make the
                same choice here, so there is no default that is right for both and the
                cheap one is the default.
            do_optimization: Geometry-optimise before the ESP calculation. Only meaningful
                with a ``gaussian_method``, and multiplies its cost.
            reuse: Return an existing successful build instead of rebuilding, but only when
                its manifest records the same protein and ligand digests. A directory named
                for a molecule is not evidence that it holds that molecule.
        """

        if not self.environment.ready:
            raise SimulationError(
                "refusing to start a build: "
                + (
                    f"{', '.join(self.environment.missing)} not found on the path of "
                    f"{self.environment.interpreter}"
                    if self.environment.missing
                    else "no gmx found"
                )
                + ". Naming the absent tool now is worth more than PRISM failing an hour "
                "into a parameterisation."
            )
        protein_path = Path(protein).expanduser().resolve()
        ligand_path = Path(ligand).expanduser().resolve()
        for label, path in (("protein", protein_path), ("ligand", ligand_path)):
            if not path.is_file():
                raise SimulationError(f"no {label} file at {path}")

        output_dir = self.workspace / run_id
        protein_digest, ligand_digest = _digest(protein_path), _digest(ligand_path)
        if output_dir.exists() and reuse and not overwrite:
            # A campaign resuming after a crash wants the build it already paid for. But
            # reuse is only honest if the inputs are the same inputs: a directory named for
            # a molecule is not evidence that it holds that molecule, and the manifest is
            # the only thing that says which bytes went in.
            existing = self._reusable(output_dir, protein_digest, ligand_digest)
            if existing is not None:
                return existing
        if output_dir.exists() and not overwrite:
            raise SimulationError(
                f"{output_dir} already exists and could not be reused. PRISM's build steps "
                "skip when their product is present, so building into it would measure the "
                "skip rather than the build; pass overwrite=True to mean it."
            )
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)

        # Two places, not one, and the split is PRISM's rather than a choice made here.
        # PRISMBuilder takes the force fields and the charge method as constructor
        # arguments; the protonation method is not a constructor argument at all and is
        # set on builder.config afterwards. Passing it as a kwarg fails with
        # "unexpected keyword argument 'protonation'" after the ligand has been
        # parameterised, which is a long way past where the mistake was made.
        kwargs: dict[str, object] = {
            "ligand_forcefield": ligand_forcefield,
            "forcefield": forcefield,
        }
        if gaussian_method is not None:
            kwargs["gaussian_method"] = gaussian_method
            kwargs["do_optimization"] = do_optimization
        # Only the protonation method. PRISM's own MCP layer writes temperature, salt
        # concentration, box distance, box shape, pressure, timestep and both
        # equilibration lengths into the config on every call; those belong to the
        # protocol and an orchestration layer that rewrites them is choosing the
        # simulation on the operator's behalf. Everything unnamed here keeps PRISM's
        # own default.
        config: list[tuple[str, str, object]] = [("protonation", "method", protonation)]
        arguments: dict[str, object] = {
            **kwargs,
            "config_overrides": {f"{section}.{key}": value for section, key, value in config},
        }
        program = (
            "import json, sys\n"
            "from prism.builder.core import PRISMBuilder\n"
            "protein, ligand, out = sys.argv[1], sys.argv[2], sys.argv[3]\n"
            "spec = json.loads(sys.argv[4])\n"
            "builder = PRISMBuilder(protein, ligand, output_dir=out, **spec['kwargs'])\n"
            "for section, key, value in spec['config']:\n"
            "    builder.config.setdefault(section, {})[key] = value\n"
            "builder.run()\n"
        )
        spec = json.dumps({"kwargs": kwargs, "config": config})
        captured = output_dir / "etalon_build.log"
        completed = subprocess.run(
            [
                str(self.environment.interpreter),
                "-c",
                program,
                str(protein_path),
                str(ligand_path),
                str(output_dir),
                spec,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=self.environment.exported(asset_root=self.infra.import_root),
        )
        captured.write_text(
            f"$ prism.system(...) -> {output_dir}\n--- stdout ---\n{completed.stdout}\n"
            f"--- stderr ---\n{completed.stderr}\n",
            encoding="utf-8",
        )

        md_dir = output_dir / "GMX_PROLIG_MD"
        # Judged by its products, for the same reason the driver is: the build's own
        # return is not evidence that a topology exists.
        built = (md_dir / "topol.top").is_file() and (md_dir / "solv_ions.gro").is_file()
        record = BuildRecord(
            run_id=run_id,
            output_dir=output_dir,
            md_dir=md_dir,
            protein_sha256=protein_digest,
            ligand_sha256=ligand_digest,
            arguments=arguments,
            environment=self.environment,
            infra=self.infra.provenance(),
            built=built,
            detail=(
                ""
                if built
                else "no topol.top and solv_ions.gro in GMX_PROLIG_MD; see the captured "
                f"output at {captured.name} (exit {completed.returncode})"
            ),
            stdout_path=captured,
        )
        (output_dir / self.MANIFEST).write_text(
            json.dumps({"build": record.as_dict()}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return record

    def _reusable(
        self, output_dir: Path, protein_digest: str, ligand_digest: str
    ) -> BuildRecord | None:
        """An existing build worth keeping, or ``None`` with nothing said.

        Silent on failure by design: every reason to refuse reuse ends in the same action,
        which is to build. Raising here would turn "I could not confirm the old build" into
        an error the caller has to handle in order to do the obvious thing.
        """

        try:
            manifest = self.manifest_of(output_dir)
        except SimulationError:
            return None
        build = manifest.get("build")
        if not isinstance(build, Mapping) or not build.get("built"):
            return None
        inputs = build.get("inputs")
        if not isinstance(inputs, Mapping):
            return None
        if (
            inputs.get("protein_sha256") != protein_digest
            or inputs.get("ligand_sha256") != ligand_digest
        ):
            return None
        md_dir = output_dir / "GMX_PROLIG_MD"
        if not ((md_dir / "topol.top").is_file() and (md_dir / "localrun.sh").is_file()):
            return None
        return BuildRecord(
            run_id=str(build.get("run_id") or output_dir.name),
            output_dir=output_dir,
            md_dir=md_dir,
            protein_sha256=protein_digest,
            ligand_sha256=ligand_digest,
            arguments=dict(build.get("arguments") or {}),
            environment=self.environment,
            infra=dict(build.get("infra") or {}),
            built=True,
            detail="reused: the manifest records the same protein and ligand digests",
            stdout_path=output_dir / "etalon_build.log",
        )

    # -- drive --------------------------------------------------------------

    def drive(
        self,
        record: BuildRecord,
        *,
        stages: Sequence[str] = ("em", "nvt", "npt"),
        timeout: int = 86_400,
    ) -> DriveRecord:
        """Run the driver script, then judge each stage by whether its product exists.

        ``stages`` names what the caller requires, and defaults to equilibration only.
        The script always attempts production as well -- it is one file and ETALON does
        not edit it -- so requesting less means a shorter list is checked, not a shorter
        run. A campaign that wants equilibration alone should set the production length
        when building.
        """

        unknown = set(stages) - {name for name, _, _ in STAGE_PRODUCTS}
        if unknown:
            raise SimulationError(
                f"unknown stage(s) {sorted(unknown)}; the driver has "
                f"{[name for name, _, _ in STAGE_PRODUCTS]}"
            )
        script = record.md_dir / "localrun.sh"
        if not script.is_file():
            raise SimulationError(
                f"no localrun.sh in {record.md_dir}. The build did not complete, and "
                "driving a system that was never built would produce a failure report "
                "about the wrong thing."
            )

        captured = record.output_dir / "etalon_drive.log"
        # Caught, not raised. The script always attempts production and PRISM's default
        # production length is 500 ns, so a campaign that wanted equilibration hits the
        # limit as a matter of course -- with em, nvt and npt already on disk. Letting the
        # exception escape would discard the record of work that really happened.
        timed_out = False
        try:
            completed = subprocess.run(
                ["bash", str(script)],
                cwd=str(record.md_dir),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=self.environment.exported(asset_root=self.infra.import_root),
            )
            exit_code: int | None = completed.returncode
            text = completed.stdout + "\n" + completed.stderr
        except subprocess.TimeoutExpired as expired:
            timed_out, exit_code = True, None

            def _text(stream: object) -> str:
                if stream is None:
                    return ""
                if isinstance(stream, bytes):
                    return stream.decode("utf-8", "replace")
                return str(stream)

            text = (
                f"{_text(expired.stdout)}\n{_text(expired.stderr)}\n"
                f"--- killed by ETALON after {timeout}s ---\n"
            )
        captured.write_text(text, encoding="utf-8")

        statuses = tuple(
            StageStatus(
                stage=name,
                tpr=(record.md_dir / tpr).is_file(),
                product=(record.md_dir / product).is_file(),
            )
            for name, tpr, product in STAGE_PRODUCTS
        )
        drive = DriveRecord(
            run_id=record.run_id,
            exit_code=exit_code,
            stages=statuses,
            warnings=read_warnings(text),
            stdout_path=captured,
            requested=tuple(stages),
            timed_out=timed_out,
        )
        manifest_path = record.output_dir / self.MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["drive"] = drive.as_dict()
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return drive

    # -- read back ----------------------------------------------------------

    @staticmethod
    def manifest_of(output_dir: str | Path) -> Mapping[str, object]:
        """The ETALON manifest written beside a build, for a campaign resuming later."""

        path = Path(output_dir).expanduser().resolve() / Simulate.MANIFEST
        if not path.is_file():
            raise SimulationError(
                f"no {Simulate.MANIFEST} in {output_dir}: this directory was not produced "
                "through ETALON, so nothing records what it was built from."
            )
        return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "BindingEnergy",
    "BuildRecord",
    "Materialized",
    "DriveRecord",
    "Environment",
    "REQUIRED_TOOLS",
    "STAGE_PRODUCTS",
    "SimulationError",
    "Simulate",
    "StageStatus",
    "Warnings",
    "discover",
    "materialize_ligands",
    "read_binding_energy",
    "read_warnings",
]
