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

**Failure detection by products AND controlled termination.** ``localrun.sh`` has no ``set -e``, so
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
- A run killed by a wall-clock limit has **no normal successful exit**, which is the common case,
  because PRISM's default production length is 500 ns and a campaign that wanted
  equilibration will always hit the limit.

One bit about the last command cannot distinguish those, and none of them says *which*
stages finished. The products do; clean termination is additionally required before the
whole execution is successful. Timed-out partial products remain evidence, not admitted
affinity labels. No PRISM edits are required because the script
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
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
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


def _identity(path: Path) -> tuple[int, int] | None:
    """Size and modification time, or None when the file is not there.

    Cheap on purpose. ``_reusable`` digests the three build products and should, because it
    decides whether to spend a build; this runs after every drive attempt and a production
    trajectory is tens of gigabytes. The question is narrower than "are these the same
    bytes" -- it is "did this attempt write this file" -- and a stage that reruns rewrites
    its product, which moves the modification time.
    """

    try:
        status = path.stat()
    except OSError:
        return None
    return (status.st_size, status.st_mtime_ns)


def _run_directory(workspace: Path, run_id: str) -> Path:
    """A run owns one real child directory, never a caller-selected deletion target."""

    if (
        not isinstance(run_id, str)
        or not run_id.strip()
        or run_id in {".", ".."}
        or run_id == ".etalon_execution_locks"
        or any(character in run_id for character in ("/", "\\", "\0"))
        or Path(run_id).is_absolute()
    ):
        raise SimulationError("run_id must be a nonempty single path component")
    root = workspace.resolve()
    target = root / run_id
    if target.is_symlink() or target.resolve().parent != root:
        raise SimulationError("run directory must not be a symlink or leave the workspace")
    return target


def _write_manifest(path: Path, body: Mapping[str, object]) -> None:
    """Replace the whole manifest atomically; a crash must not truncate old evidence."""

    data = json.dumps(body, indent=2, sort_keys=True) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".etalon-manifest-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


_PROCESS_GROUPS_SUPPORTED = os.name == "posix"


def _exclusive_run(operation):
    """Cooperating controllers serialize one run, including overwrite and recovery.

    Lock files are never unlinked on release: replacing their inode would allow a second
    controller to acquire a different lock while the first still owns the original one.
    flock is released by the OS if this controller dies; the pending manifest still
    demands explicit recovery. This does not sandbox external writers or remote jobs.
    """

    @wraps(operation)
    def guarded(self, *args, **kwargs):
        if not _PROCESS_GROUPS_SUPPORTED:
            raise SimulationError("controlled PRISM execution requires POSIX process groups and file locks")
        if not hasattr(os, "O_NOFOLLOW"):
            raise SimulationError("safe execution locks require O_NOFOLLOW; refusing unsupported lock semantics")
        if not self.environment.ready:
            raise SimulationError("refusing to start: " + ", ".join(self.environment.missing or ("no gmx found",)))
        record = args[0] if args else kwargs.get("record")
        run_id = kwargs.get("run_id") if operation.__name__ == "build" else getattr(record, "run_id", None)
        output = _run_directory(self.workspace, run_id)
        lock_directory = output.parent / ".etalon_execution_locks"
        if lock_directory.is_symlink():
            raise SimulationError("execution lock directory must not be a symlink")
        lock_directory.mkdir(exist_ok=True)
        lock_path = lock_directory / (hashlib.sha256(run_id.encode()).hexdigest() + ".lock")
        import fcntl

        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as error:
            raise SimulationError("cannot safely open the run execution lock") from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SimulationError("run execution lock must be a regular file")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SimulationError("another controller owns this run execution lock") from error
            return operation(self, *args, **kwargs)
        finally:
            os.close(descriptor)

    return guarded


def _text(stream: object) -> str:
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", "replace")
    return str(stream)


@dataclass(frozen=True, slots=True)
class _CapturedRun:
    returncode: int | None
    stdout: str
    stderr: str
    status: str = "completed"
    elapsed_seconds: float = 0.0
    process_group: int | None = None
    cleanup: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status, "exit_code": self.returncode,
            "elapsed_seconds": self.elapsed_seconds, "process_group": self.process_group,
            "cleanup": dict(self.cleanup), "cost": None,
            "cost_status": "not_started" if self.status == "launch_failed" else "unknown",
            "recovery_required": self.status != "completed",
            "scope": "owned POSIX process group only; detached sessions and remote jobs are not controlled",
        }


def _run_owned(
    command: Sequence[str], *, timeout: float, env: Mapping[str, str], cwd: str | None = None,
    process_observer: Callable[[int], None] | None = None,
) -> _CapturedRun:
    """Bound one owned POSIX session, retaining partial output on timeout or interruption.

    The group id is always the PID returned by our own start_new_session Popen. No
    process-name discovery, caller-supplied pid, or signal to the caller's group is used.
    Deliberately detached sessions/remote schedulers require a different execution adapter.
    """

    if not _PROCESS_GROUPS_SUPPORTED:
        raise SimulationError("controlled PRISM execution requires POSIX process groups; this platform is unsupported")
    started = time.monotonic()
    gate_read = gate_write = None
    try:
        launch = list(command)
        descriptor_options = {}
        if process_observer is not None:
            # The scientific command cannot start until its owner is durably recorded.
            # A killed parent closes the sole write end: the waiting child then exits
            # without executing anything. exec preserves the recorded PID/session.
            gate_read, gate_write = os.pipe()
            launcher = (
                "import os,sys\n"
                "fd=int(sys.argv[1])\n"
                "try: allowed=os.read(fd,1)\n"
                "finally: os.close(fd)\n"
                "if allowed!=b'1': sys.exit(125)\n"
                "os.execvpe(sys.argv[2],sys.argv[2:],os.environ)\n"
            )
            launch = [sys.executable, "-c", launcher, str(gate_read), *launch]
            descriptor_options = {"pass_fds": (gate_read,)}
        process = subprocess.Popen(
            launch, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", start_new_session=True,
            env=dict(env), cwd=cwd, **descriptor_options,
        )
    except BaseException as error:
        for descriptor in (gate_read, gate_write):
            if descriptor is not None:
                os.close(descriptor)
        if isinstance(error, OSError):
            return _CapturedRun(None, "", str(error), "launch_failed", time.monotonic() - started)
        raise
    status = "completed"
    cleanup: dict[str, object] = {}
    stdout = stderr = ""
    try:
        if gate_read is not None:
            os.close(gate_read)
            gate_read = None
        # Register ownership while cleanup is already armed. A failed journal write
        # must terminate the process we just started, not leave untracked compute.
        if process_observer is not None:
            process_observer(process.pid)
            os.write(gate_write, b"1")
            os.close(gate_write)
            gate_write = None
        stdout, stderr = process.communicate(timeout=timeout)
        if process.returncode != 0:
            status = "failed"
    except subprocess.TimeoutExpired as error:
        status = "timed_out"
        stdout, stderr = _text(error.stdout), _text(error.stderr)
    except KeyboardInterrupt:
        # Return an interrupted capture so the caller can persist it, then re-raise.
        status = "interrupted"
    except Exception as error:
        status = "failed"
        stderr = f"capture failed: {type(error).__name__}: {error}"
        cleanup["capture_error"] = stderr
    finally:
        for descriptor in (gate_read, gate_write):
            if descriptor is not None:
                os.close(descriptor)
        # Even a successful shell must not leave an untracked background child running.
        # This is exactly the process group created above, never our inherited group.
        try:
            if process.pid <= 1 or process.pid == os.getpgrp():
                raise RuntimeError("refusing to signal an unowned process group")
            os.killpg(process.pid, signal.SIGKILL)
            cleanup["group_kill_sent"] = True
            if status == "completed":
                status = "orphaned_children"
        except ProcessLookupError:
            cleanup["group_kill_sent"] = False
        except (OSError, RuntimeError) as error:
            cleanup["error"] = f"{type(error).__name__}: {error}"
            if status == "completed":
                status = "cleanup_failed"
        if status != "completed":
            try:
                # communicate returns the full stream, including the bytes reported in
                # TimeoutExpired; do not append them twice.
                stdout, stderr = process.communicate(timeout=2.0)
                cleanup["output_drained"] = True
            except subprocess.TimeoutExpired as error:
                stdout, stderr = _text(error.stdout), _text(error.stderr)
                cleanup["output_drained"] = False
                cleanup["error"] = "pipes remained open after owned-group kill; operator review required"
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    cleanup["leader_reaped"] = False
            cleanup.setdefault("leader_reaped", process.returncode is not None)
    return _CapturedRun(process.returncode, _text(stdout), _text(stderr), status,
                        time.monotonic() - started, process.pid, cleanup)


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

        # IDs are opaque: even a caller-supplied path must never become an output path.
        stem = f"{index:05d}_{hashlib.sha256(identifier.encode('utf-8')).hexdigest()}"
        path = target / f"{stem}.sdf"
        text = str(molblock)
        text = (text if text.endswith("\n") else text + "\n") + "$$$$\n"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(text)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_text(encoding="utf-8") != text:
                out.append(Materialized(identifier, None, "existing ligand file has different identity"))
                continue

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
    """One stage of the driver script, judged by its products and by who made them."""

    stage: str
    tpr: bool
    product: bool
    #: Whether *this* attempt wrote the product, rather than finding it already there.
    #: Default True so a record constructed without the distinction reads as it always did.
    fresh: bool = True

    @property
    def state(self) -> str:
        if self.product and self.fresh:
            return "FINISHED"
        if self.product:
            # The product is there and this attempt did not write it. Not a failure: reusing
            # an earlier stage's output is the point of resuming a drive. Not a measurement
            # this attempt made either, and the two were previously the same word.
            return "FINISHED_EARLIER"
        if self.tpr:
            # grompp worked and mdrun did not: the interesting failure, because the
            # system was buildable and the simulation was not.
            return "STARTED_NOT_FINISHED"
        return "NEVER_STARTED"

    def as_dict(self) -> dict[str, object]:
        return {"stage": self.stage, "state": self.state, "tpr": self.tpr,
                "product": self.product, "fresh": self.fresh}


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
_ENERGY_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_ENERGY_LINE = re.compile(
    rf"^[ \t]*([\w /]+?)[ \t]*=[ \t]*({_ENERGY_NUMBER})[ \t]*\+/-[ \t]*"
    rf"({_ENERGY_NUMBER})[ \t]*$", re.MULTILINE,
)


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
    if len(re.findall(r"^[ \t]*DELTA TOTAL[ \t]*=", text, re.MULTILINE)) != 1:
        return None
    matches = list(_ENERGY_LINE.finditer(text))
    names = [match.group(1).strip() for match in matches]
    # A file can contain separate GB/PB sections. There is no method selector in this
    # API, so choosing its first total (and last components) would mix estimands.
    if len(set(names)) != len(names) or names.count("DELTA TOTAL") != 1:
        return None
    if any(not math.isfinite(float(match.group(index))) for match in matches for index in (2, 3)):
        return None
    if any(float(match.group(3)) < 0 for match in matches):
        return None
    components = {match.group(1).strip(): float(match.group(2)) for match in matches}
    total = matches[names.index("DELTA TOTAL")]
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
    products_sha256: dict[str, str] = field(default_factory=dict)
    execution: dict[str, object] = field(default_factory=dict)

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
            "products_sha256": dict(self.products_sha256),
            "execution": dict(self.execution),
        }


@dataclass(frozen=True, slots=True)
class DriveRecord:
    """Driver outcome: clean termination plus products, with partial facts retained."""

    run_id: str
    #: The driver's exit status (negative for a signal), or None when no status is known.
    #: A zero exit is necessary, not sufficient: requested products must also exist.
    exit_code: int | None
    stages: tuple[StageStatus, ...]
    warnings: Warnings
    stdout_path: Path
    requested: tuple[str, ...]
    #: Set when the wall-clock limit killed the driver. Not a failure of the stages that
    #: had already finished, and the products say which those were.
    timed_out: bool = False
    execution: dict[str, object] = field(default_factory=dict)

    #: The two states that mean "the product is on disk". Both satisfy the protocol; only
    #: the first is a thing this attempt did.
    _HAVE_PRODUCT = ("FINISHED", "FINISHED_EARLIER")

    @property
    def finished(self) -> tuple[str, ...]:
        """Stages whose product exists, whoever wrote it. What every reader has meant by this."""

        return tuple(s.stage for s in self.stages if s.state in self._HAVE_PRODUCT)

    @property
    def produced(self) -> tuple[str, ...]:
        """Stages this attempt wrote a product for. A subset of ``finished``."""

        return tuple(s.stage for s in self.stages if s.state == "FINISHED")

    @property
    def inherited(self) -> tuple[str, ...]:
        """Stages whose product was already there when this attempt started.

        Read this before calling an attempt a measurement. A drive that terminates cleanly
        and produces nothing is indistinguishable from one that produced everything, if the
        only question asked is whether the files exist.
        """

        return tuple(s.stage for s in self.stages if s.state == "FINISHED_EARLIER")

    @property
    def failed(self) -> tuple[StageStatus, ...]:
        return tuple(s for s in self.stages
                     if s.stage in self.requested and s.state not in self._HAVE_PRODUCT)

    @property
    def succeeded(self) -> bool:
        """Clean termination AND every requested product; partial products remain facts.

        Compatibility: a timed-out equilibration is no longer an overall success merely
        because its requested products exist. ``finished`` retains those product facts.

        An inherited product still satisfies this, because the campaign asked for a product
        and has one, under a build identity ``_reusable`` verified. What changed is that the
        record now says which products this attempt did not write, so ``inherited`` rather
        than ``succeeded`` is the field that answers "was anything simulated here".
        """

        return (not self.timed_out and self.exit_code == 0
                and self.execution.get("status", "completed") == "completed"
                and bool(self.requested) and all(
            any(stage.stage == name and stage.state in self._HAVE_PRODUCT
                for stage in self.stages)
            for name in self.requested
        ))

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "succeeded": self.succeeded,
            "requested": list(self.requested),
            "finished": list(self.finished),
            "produced": list(self.produced),
            "inherited": list(self.inherited),
            "failed": [s.as_dict() for s in self.failed],
            "stages": [s.as_dict() for s in self.stages],
            "warnings": self.warnings.as_dict(),
            "captured_output": str(self.stdout_path),
            "execution": dict(self.execution),
            "note": (
                "Success requires clean termination and every requested stage's product. Measured on this "
                "machine: a fresh build with a broken topology exits 1; the same "
                "directory re-driven after production had completed exits 0 with em, nvt "
                "and npt all failed and 23 error lines in the log; a run killed by the "
                "wall clock has no normal successful exit. One bit about the last command "
                "cannot tell those apart, and none of them says which stages finished. "
                "`inherited` names products that were on disk before this attempt started: "
                "the protocol is satisfied either way, but an attempt that wrote nothing is "
                "not a simulation of anything, and `succeeded` alone cannot say which it was."
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
        process_observer: Callable[[int], None] | None = None,
    ) -> None:
        if process_observer is not None and not callable(process_observer):
            raise ValueError("process_observer must be callable")
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.environment = environment
        self.infra = infra or load("prism")
        self.process_observer = process_observer

    # -- build --------------------------------------------------------------

    @_exclusive_run
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
        production_ns: float | None = None,
        reuse: bool = False,
        overwrite: bool = False,
        timeout: int = 14_400,
        confirm_recovery: bool = False,
        recovery_reason: str | None = None,
    ) -> BuildRecord:
        """Build one protein-ligand system, and write the manifest PRISM does not.

        The defaults are PRISM's canonical ones, and box, salt and temperature are not
        touched at all: those belong to the protocol, not to an orchestration layer. What
        is added is identity.

        Args:
            production_ns: Explicit production duration in nanoseconds. ``None`` keeps
                PRISM's default (500 ns in the pinned asset). This is recorded in the
                protocol identity; ``drive(stages=...)`` only selects products to check
                and does not shorten the script or replace this setting.
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
                its manifest records the same inputs, scientific arguments, environment
                and infrastructure. A directory name is not evidence of protocol identity.
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
        self._check_recovery(None, confirm_recovery, recovery_reason)
        if not _PROCESS_GROUPS_SUPPORTED:
            raise SimulationError("controlled PRISM execution requires POSIX process groups")
        protein_path = Path(protein).expanduser().resolve()
        ligand_path = Path(ligand).expanduser().resolve()
        for label, path in (("protein", protein_path), ("ligand", ligand_path)):
            if not path.is_file():
                raise SimulationError(f"no {label} file at {path}")

        output_dir = _run_directory(self.workspace, run_id)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise SimulationError("timeout must be finite and positive")
        if production_ns is not None and (
            isinstance(production_ns, bool) or not isinstance(production_ns, (int, float))
            or not math.isfinite(production_ns) or production_ns <= 0
        ):
            raise SimulationError("production_ns must be finite and positive, or None for PRISM defaults")
        protein_digest, ligand_digest = _digest(protein_path), _digest(ligand_path)

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
        # Only explicitly selected protocol parameters. PRISM's MCP layer writes temperature, salt
        # concentration, box distance, box shape, pressure, timestep and both
        # equilibration lengths into the config on every call; those belong to the
        # protocol and an orchestration layer that rewrites them is choosing the
        # simulation on the operator's behalf. Everything unnamed here keeps PRISM's
        # own default.
        config: list[tuple[str, str, object]] = [("protonation", "method", protonation)]
        if production_ns is not None:
            config.append(("simulation", "production_time_ns", float(production_ns)))
        arguments: dict[str, object] = {
            **kwargs,
            "config_overrides": {f"{section}.{key}": value for section, key, value in config},
        }
        if output_dir.exists() and reuse and not overwrite:
            existing = self._reusable(output_dir, protein_digest, ligand_digest, arguments=arguments)
            if existing is not None:
                return existing
        if output_dir.exists() and not overwrite:
            raise SimulationError(
                f"{output_dir} already exists and could not be reused. PRISM's build steps "
                "skip when their product is present, so building into it would measure the "
                "skip rather than the build; pass overwrite=True to mean it."
            )
        if output_dir.exists():
            try:
                previous_manifest = self.manifest_of(output_dir)
            except SimulationError:
                previous_manifest = {}
            self._check_recovery(previous_manifest.get("build"), confirm_recovery, recovery_reason)
            self._check_recovery(previous_manifest.get("drive"), confirm_recovery, recovery_reason)
            if any(path.is_relative_to(output_dir) for path in (protein_path, ligand_path)):
                raise SimulationError("refusing to overwrite a run directory containing its input files")
            if not output_dir.is_dir():
                raise SimulationError("run output exists but is not a directory")
            shutil.rmtree(output_dir)
        try:
            output_dir.mkdir(parents=True)
        except FileExistsError as error:
            raise SimulationError("run directory was claimed by another build") from error
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
        pending = BuildRecord(
            run_id=run_id, output_dir=output_dir, md_dir=output_dir / "GMX_PROLIG_MD",
            protein_sha256=protein_digest, ligand_sha256=ligand_digest, arguments=arguments,
            environment=self.environment, infra=self.infra.provenance(), built=False,
            detail="execution outcome pending; operator review required before recovery",
            stdout_path=captured, execution={"status": "running", "cost": None,
                                            "cost_status": "unknown", "recovery_required": True},
        )
        _write_manifest(output_dir / self.MANIFEST, {"build": pending.as_dict()})
        completed = _run_owned(
            [
                str(self.environment.interpreter),
                "-c",
                program,
                str(protein_path),
                str(ligand_path),
                str(output_dir),
                spec,
            ],
            timeout=timeout,
            env=self.environment.exported(asset_root=self.infra.import_root),
            **({"process_observer": self.process_observer} if self.process_observer is not None else {}),
        )
        captured.write_text(
            f"$ prism.system(...) -> {output_dir}\n--- stdout ---\n{completed.stdout}\n"
            f"--- stderr ---\n{completed.stderr}\n",
            encoding="utf-8",
        )

        md_dir = output_dir / "GMX_PROLIG_MD"
        # Products are necessary, as is a clean execution below: a successful return
        # alone is not evidence that a topology exists.
        product_names = ("topol.top", "solv_ions.gro", "localrun.sh")
        products = {
            name: _digest(md_dir / name) for name in product_names
            if (md_dir / name).is_file() and (md_dir / name).resolve().is_relative_to(output_dir)
        }
        built = len(products) == len(product_names)
        try:
            inputs_unchanged = (_digest(protein_path), _digest(ligand_path)) == (
                protein_digest, ligand_digest,
            )
        except OSError:
            inputs_unchanged = False
        built = built and inputs_unchanged and completed.status == "completed" and completed.returncode == 0
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
                else f"execution {completed.status}; compute cost is unknown; see {captured.name}"
                if completed.status != "completed"
                else "input files changed during the build; products cannot attest the requested inputs"
                if not inputs_unchanged
                else "missing topology, coordinates or driver in GMX_PROLIG_MD; see the captured "
                f"output at {captured.name} (exit {completed.returncode})"
            ),
            stdout_path=captured,
            products_sha256=products,
            execution={**completed.as_dict(), "recovery_reason": recovery_reason if confirm_recovery else None},
        )
        _write_manifest(output_dir / self.MANIFEST, {"build": record.as_dict()})
        if completed.status == "interrupted":
            raise KeyboardInterrupt("PRISM build interrupted; outcome recorded, explicit recovery required")
        return record

    @staticmethod
    def _check_recovery(previous: object, confirmed: bool, reason: str | None) -> None:
        if not isinstance(confirmed, bool):
            raise SimulationError("confirm_recovery must be an explicit boolean")
        if confirmed and (not isinstance(reason, str) or len(reason.strip()) < 12):
            raise SimulationError("confirmed recovery requires a meaningful operator recovery_reason")
        if reason is not None and not confirmed:
            raise SimulationError("recovery_reason requires confirm_recovery=True")
        execution = previous.get("execution") if isinstance(previous, Mapping) else None
        pending = isinstance(execution, Mapping) and (
            execution.get("recovery_required") is True or execution.get("status") == "running"
        )
        if pending and not confirmed:
            raise SimulationError(
                "previous execution requires operator review; verify that prior compute is stopped, "
                "then pass confirm_recovery=True and recovery_reason"
            )

    def _reusable(
        self, output_dir: Path, protein_digest: str, ligand_digest: str,
        *, arguments: Mapping[str, object] | None = None,
    ) -> BuildRecord | None:
        """An existing build worth keeping, or ``None`` with nothing said.

        Silent on failure by design: the public caller refuses reuse and never
        implicitly rebuilds. Overwrite and interrupted-run recovery must be explicit.
        """

        try:
            manifest = self.manifest_of(output_dir)
        except SimulationError:
            return None
        build = manifest.get("build")
        if not isinstance(build, Mapping) or build.get("built") is not True:
            return None
        inputs = build.get("inputs")
        if not isinstance(inputs, Mapping):
            return None
        if (
            inputs.get("protein_sha256") != protein_digest
            or inputs.get("ligand_sha256") != ligand_digest
        ):
            return None
        # The public build path always supplies arguments. The digest-only private
        # inspection mode remains useful for reading old manifests, not dispatching them.
        if arguments is not None and (
            build.get("arguments") != dict(arguments)
            or build.get("environment") != self.environment.as_dict()
            or build.get("infra") != self.infra.provenance()
            or build.get("run_id") != output_dir.name
        ):
            return None
        execution = build.get("execution")
        if isinstance(execution, Mapping) and execution.get("status") not in {None, "completed"}:
            return None
        md_dir = output_dir / "GMX_PROLIG_MD"
        if not ((md_dir / "topol.top").is_file() and (md_dir / "localrun.sh").is_file()):
            return None
        if arguments is not None and not (md_dir / "solv_ions.gro").is_file():
            return None
        if arguments is not None:
            if md_dir.is_symlink() or any(
                not (md_dir / name).resolve().is_relative_to(output_dir)
                for name in ("topol.top", "solv_ions.gro", "localrun.sh")
            ):
                return None
            try:
                products = {name: _digest(md_dir / name)
                            for name in ("topol.top", "solv_ions.gro", "localrun.sh")}
            except OSError:
                return None
            if build.get("products_sha256") != products:
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
            detail="reused: the manifest matches the requested input and protocol identity",
            stdout_path=output_dir / "etalon_build.log",
            products_sha256=dict(build.get("products_sha256") or {}),
            execution=dict(build.get("execution") or {}),
        )

    # -- drive --------------------------------------------------------------

    @_exclusive_run
    def drive(
        self,
        record: BuildRecord,
        *,
        stages: Sequence[str] = ("em", "nvt", "npt"),
        timeout: int = 86_400,
        confirm_recovery: bool = False,
        recovery_reason: str | None = None,
    ) -> DriveRecord:
        """Require clean execution and each requested product; preserve partial outcomes.

        ``stages`` names what the caller requires, and defaults to equilibration only.
        The script always attempts production as well -- it is one file and ETALON does
        not edit it -- so requesting less means a shorter list is checked, not a shorter
        run. A campaign that wants equilibration alone should set the production length
        when building.
        """

        if not stages or len(set(stages)) != len(stages):
            raise SimulationError("required stages must be nonempty and unique")
        if not _PROCESS_GROUPS_SUPPORTED:
            raise SimulationError("controlled PRISM execution requires POSIX process groups")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise SimulationError("timeout must be finite and positive")
        unknown = set(stages) - {name for name, _, _ in STAGE_PRODUCTS}
        if unknown:
            raise SimulationError(
                f"unknown stage(s) {sorted(unknown)}; the driver has "
                f"{[name for name, _, _ in STAGE_PRODUCTS]}"
            )
        output_dir = _run_directory(self.workspace, record.run_id)
        if record.output_dir != output_dir or record.md_dir != output_dir / "GMX_PROLIG_MD":
            raise SimulationError("build record paths do not match its run identity")
        verified = self._reusable(output_dir, record.protein_sha256, record.ligand_sha256,
                                  arguments=record.arguments)
        if verified is None or not record.built:
            raise SimulationError("build manifest no longer matches the requested protocol or products")
        script = record.md_dir / "localrun.sh"
        if not script.is_file():
            raise SimulationError(
                f"no localrun.sh in {record.md_dir}. The build did not complete, and "
                "driving a system that was never built would produce a failure report "
                "about the wrong thing."
            )

        manifest_path = record.output_dir / self.MANIFEST
        manifest = dict(self.manifest_of(record.output_dir))
        previous = manifest.get("drive")
        self._check_recovery(previous, confirm_recovery, recovery_reason)
        attempts = list(manifest.get("drive_attempts") or ([previous] if previous else []))
        if any(not isinstance(attempt, Mapping) for attempt in attempts):
            raise SimulationError("drive attempt history is malformed")
        attempt_number = len(attempts) + 1
        captured = record.output_dir / ("etalon_drive.log" if attempt_number == 1
                                       else f"etalon_drive.{attempt_number:04d}.log")
        pending = DriveRecord(
            record.run_id, None, (), Warnings(0), captured, tuple(stages),
            execution={"status": "running", "cost": None, "cost_status": "unknown",
                       "recovery_required": True, "recovery_reason": recovery_reason},
        ).as_dict()
        attempts.append(pending)
        manifest["drive"], manifest["drive_attempts"] = pending, attempts
        _write_manifest(manifest_path, manifest)
        # Taken before the driver starts, because afterwards there is no way to tell a product
        # this attempt wrote from one the previous attempt left behind. The build path makes the
        # same comparison with digests at _reusable(); products here are too large for that.
        before = {name: (_identity(record.md_dir / tpr), _identity(record.md_dir / product))
                  for name, tpr, product in STAGE_PRODUCTS}
        completed = _run_owned(
            ["bash", str(script)], cwd=str(record.md_dir), timeout=timeout,
            env=self.environment.exported(asset_root=self.infra.import_root),
            **({"process_observer": self.process_observer} if self.process_observer is not None else {}),
        )
        text = (f"{completed.stdout}\n{completed.stderr}\n"
                f"--- execution {completed.status}; requested limit {timeout}s ---\n")
        captured.write_text(text, encoding="utf-8")

        statuses = tuple(
            StageStatus(
                stage=name,
                tpr=(record.md_dir / tpr).is_file(),
                product=(record.md_dir / product).is_file(),
                fresh=_identity(record.md_dir / product) != before[name][1],
            )
            for name, tpr, product in STAGE_PRODUCTS
        )
        drive = DriveRecord(
            run_id=record.run_id,
            exit_code=completed.returncode,
            stages=statuses,
            warnings=read_warnings(text),
            stdout_path=captured,
            requested=tuple(stages),
            timed_out=completed.status == "timed_out",
            execution={**completed.as_dict(), "recovery_reason": recovery_reason if confirm_recovery else None},
        )
        manifest["drive"] = drive.as_dict()
        attempts[-1] = manifest["drive"]
        _write_manifest(manifest_path, manifest)
        if completed.status == "interrupted":
            raise KeyboardInterrupt("PRISM drive interrupted; outcome recorded, explicit recovery required")
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
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise SimulationError(f"cannot read valid run manifest at {path}") from error
        if not isinstance(manifest, Mapping):
            raise SimulationError(f"run manifest at {path} is not an object")
        return manifest


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
